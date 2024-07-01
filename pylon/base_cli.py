'''
a command-line task dispatcher
- includes a default parser based on the argparse module
- includes a logger based on the logging module (+ stub functions for easy logging)
- dispatch() output parameter
  None             => cfg=NULL     , stderr/stdout=None
  sys.std, sys.std => cfg=PIPE     , stderr/stdout=sys.std
  stream , None    => cfg=PIPE/NULL, stderr/stdout=stream/None
  sys.std, None    => cfg=PIPE/NULL, stderr/stdout=sys.std/None
  None   , None    => cfg=None     , stderr/stdout=None
'''

import argparse
import asyncio
import contextlib
import datetime
import functools
import io
import logging
import os
import pylon
import sys

# =====================================================================================================================
# decorators
# =====================================================================================================================
def log_exec_time(func):
    'async method decorator to log execution time'
    # keep original attributes for decorated function (eg, __name__, __doc__, ...)
    @functools.wraps(func)
    async def async_method_wrapper(self, *args, **kwargs):
        t1 = datetime.datetime.now()
        ret = await func(self, *args, **kwargs)
        self.logger.info(func.__name__ + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')
        return ret
    return async_method_wrapper

# =====================================================================================================================
# classes
# =====================================================================================================================
class prefixed_stringio(io.StringIO):
    def __init__(self, _stream, _newline=True):
        super().__init__()
        self.__dict__.update(locals())
    
    def write(self, s):
        for idx,line in enumerate(s.splitlines(keepends=True)):
            task_name = asyncio.current_task().get_name()
            if (idx == 0 and
                not self._newline or
                task_name == 'MainTask'):
                self._stream.write(line)
            else:
                self._stream.write(f'{task_name}: {line}')
        self._newline = s.endswith(os.linesep)

class base_cli():
    __doc__ = sys.modules[__name__].__doc__

    @property
    def args(self):
        # just return None before parser init
        return getattr(self, '_args', None)
    
    @property
    def logger(self):
        return self._logger_adapter
    
    @property
    def parser(self):
        return self._parser
    
    def __init__(self):
        # set logger name to class name
        self._logger = logging.getLogger(self.__class__.__name__)

        # define format of logger output
        self._formatter = logging.Formatter('%(task_str)s### %(name)s(%(asctime)s) %(levelname)s: %(message)s')
        class task_name_adapter(logging.LoggerAdapter):
            def process(self, msg, kwargs):
                task_name = asyncio.current_task().get_name()
                task_str = ''
                if task_name != 'MainTask':
                    task_str = f'{task_name}: '
                kwargs.setdefault('extra', {})['task_str'] = task_str
                return msg, kwargs
        self._logger_adapter = task_name_adapter(self._logger)

        # stdout/stderr logging
        handler = logging.StreamHandler(sys.__stdout__)
        handler.setFormatter(self._formatter)
        handler.setLevel(logging.DEBUG)
        handler.addFilter(lambda x: x.levelno < logging.WARNING)
        self._logger.addHandler(handler)
        handler = logging.StreamHandler(sys.__stderr__)
        handler.setFormatter(self._formatter)
        handler.setLevel(logging.WARNING)
        self._logger.addHandler(handler)
        
        self._parser = argparse.ArgumentParser(description=self.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        # define the common basic set of arguments
        self.parser.add_argument('--dry_run', action='store_true',
                                 help='switch to passive behavior')
        self.parser.add_argument('-q', action='count', dest='quiet', default=0,
                                 help='quiet output (multiply for more silence)')
        self.parser.add_argument('-v', action='count', dest='verbosity', default=0,
                                 help='verbose output (multiply for more verbosity)')

    async def setup(self):
        self._args = self.parser.parse_args()

        # determine default verbosity behavior
        level = logging.INFO
        if self.args.verbosity > 0 or self.args.dry_run:
            level = logging.DEBUG

        # quiet switch takes precedence
        if self.args.quiet > 1:
            level = logging.ERROR
        elif self.args.quiet > 0:
            level = logging.WARNING
        self.logger.setLevel(level)

        # - add prefix by default for interleaved output of parallel coroutines
        # - in setup() to avoid prefixing when printing parser help string
        sys.stdout = prefixed_stringio(sys.__stdout__)
        sys.stderr = prefixed_stringio(sys.__stderr__)

    async def cleanup(self):
        pass

    async def dispatch(self, cmd, **kwargs):
        self.logger.debug(cmd)

        name = kwargs.get('name', None)
        output = kwargs.get('output', (sys.stderr, sys.stdout))
        passive = kwargs.get('passive', False)
        
        # output redirection logic
        stderr = stdout = None
        stderr_cfg = stdout_cfg = asyncio.subprocess.DEVNULL
        if output is not None:
            stderr = output[0]
            stdout = output[1]
            if stderr is not None and (self.args.quiet <= 1 or isinstance(stderr, io.StringIO)):
                stderr_cfg = asyncio.subprocess.PIPE
            if stdout is not None and (self.args.quiet <= 0 or isinstance(stdout, io.StringIO)):
                stdout_cfg = asyncio.subprocess.PIPE
            if stderr is None and stdout is None:
                stderr_cfg = stdout_cfg = None

        if not self.args.dry_run or passive:
            if name is None:
                name = asyncio.current_task().get_name()
             
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stderr=stderr_cfg,
                stdout=stdout_cfg)
             
            async def reader(instr, outstr):
                async for line in instr:
                    outstr.write(line.decode())
                # reset stream in case of StringIO output
                # FIXME too much overhead here? maybe in try,finally inside loop?
                outstr.seek(0)
            while True:
                if stderr is None and stdout is None:
                    # no pipe case
                    await proc.wait()
                else:
                    with contextlib.suppress(asyncio.TimeoutError):
                        # FIXME timeout short enough for high-velocity output producers?
                        # limit determines the buffer size limit used by the returned StreamReader instance. By default the limit is set to 64 KiB.
                        async with asyncio.timeout(0.1):
                            async with asyncio.TaskGroup() as tg:
                                if stderr_cfg is asyncio.subprocess.PIPE:
                                    tg.create_task(reader(proc.stderr, stderr), name=name)
                                if stdout_cfg is asyncio.subprocess.PIPE:
                                    tg.create_task(reader(proc.stdout, stdout), name=name)

                if proc.returncode is not None:
                    if proc.returncode != 0:
                        raise pylon.script_error(f'retcode {proc.returncode} when executing "{cmd}"', proc)
                    break

    async def dispatch_group(self, task_dicts):
        async with asyncio.TaskGroup() as tg:
            for task_dict in task_dicts:
                tg.create_task(task_dict['task'],
                               name=task_dict.get('name', asyncio.current_task().get_name()))
    
    async def run_task(self):
        try:
            await self.setup()
            await self.run_core()

        # FIXME this breaks reporting of solana RPC errors, use this as a testbench
        # handle ExceptionGroup
        except* Exception as eg:
            def handle_exception_group_leaves(eg):
                if hasattr(eg, 'exceptions'):
                    for e in eg.exceptions:
                        handle_exception_group_leaves(e)
                else:
                    # custom exception handler: log exceptions, different verbosity for traceback
                    self.logger.error(eg)
                    # FIXME something's fishy here
                    import traceback
                    self.logger.debug(''.join(traceback.format_tb(eg.__traceback__)))
            handle_exception_group_leaves(eg)
            
        finally:
            await self.cleanup()
            
            # restore default stream handlers
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__

    async def run(self):
        return await asyncio.create_task(self.run_task(), name='MainTask')
