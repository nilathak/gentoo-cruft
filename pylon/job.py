'''
provides a job class which contains a generic wrapper for subprocess invocation.

- dispatch jobs non-blocking for multithreaded operation (blocking=False)
- allow forced execution of subprocess commands even during dry-run (passive=True)
- it re-uses an existing ui class for logging if provided
- it selects a different logger based on number of concurrent threads
- it allows different handling of subprocess pipes (output option)
    setting   | stdout            | stderr            | comments
    --------------------------------------------------------------------
    None      | pipe->var         | pipe->var         | var are the self.stdout and self.stderr variables
    "stdout"  | pipe->var->stdout | null              |
    "stderr"  | null              | pipe->var->stderr |
    "both"    | pipe->var->stdout | pipe->var->stderr | default
    "nopipes" | stdout            | stderr            | direct output without pipes (self.stdout/self.stderr remain empty)
'''

import os
import subprocess
import sys
import threading

class job(object):

    @property
    def exc_info(self):
        return self._exc_info
    @property
    def ret_val(self):
        return self._ret_val
    @property
    def stderr(self):
        return self._stderr
    @property
    def stdout(self):
        return self._stdout
    @property
    def thread(self):
        return self._thread
    @property
    def ui(self):
        return self._ui

    def __init__(self, ui, cmd, output='both', owner=None, passive=False, blocking=True, daemon=False, **kwargs):
        self.__dict__.update({'_'+k:v for k,v in locals().items() if k != 'self'})
        self._exc_info = None
        self._kwargs = kwargs
        self._ret_val = None
        self._threadname = threading.current_thread().name
        self._thread = threading.current_thread()
        if not blocking:
            self._thread = threading.Thread(target=self.exception_wrapper,
                                            daemon=self._daemon)

    def __call__(self):

        # stay in current thread for blocking jobs, otherwise fork
        if self._blocking:
            self.exception_wrapper()
        else:
            self.thread.start()
        return self

    def join(self, **kwargs):
        'join back to caller thread'
        self.thread.join(**kwargs)

    def exception_wrapper(self):
        try:

            # assign meaningful output prefixes (use parent thread id for
            # blocking jobs)
            if len(self._owner.jobs) == 0:
                self._prefix = ''
            else:
                self._prefix = self.thread.name + ': '
                for h in self.ui.logger.handlers:
                    h.setFormatter(self.ui.formatter['threaded'])

            # python function?
            if hasattr(self._cmd, '__call__'):
                self._ret_val = self._cmd(**self._kwargs)

            # no? then assume it's a string containing an external command invocation
            else:
                self.exec_cmd()
        except Exception as e:
            # - save exception context to inform caller thread
            # - print exception here to include correct thread info
            if not self._blocking:
                self._exc_info = sys.exc_info()
                self.ui.excepthook(*self.exc_info)
            else:
                raise e
        finally:
            # reset the logger format if only Mainthread will be left
            if not self._blocking and len(self._owner.jobs) <= 1:
                for h in self.ui.logger.handlers:
                    h.setFormatter(self.ui.formatter['default'])

    def exec_cmd(self):
        'execute subprocess on POSIX architectures'
        self.ui.debug(self._cmd)
        self._stdout = list()
        self._stderr = list()
        if not self.ui.args.dry_run or self._passive:

            try:

                # quiet switch takes precedence
                if self.ui.args.quiet > 1:
                    self._output = None
                elif self.ui.args.quiet > 0:
                    # do not interfere with already configured None
                    if self._output:
                        self._output = 'stderr'

                # decode output string
                stdout = subprocess.PIPE
                stderr = subprocess.PIPE
                if self._output == 'nopipes':
                    stdout = None
                    stderr = None
                elif self._output == 'stdout':
                    stderr = subprocess.DEVNULL
                elif self._output == 'stderr':
                    stdout = subprocess.DEVNULL

                self._proc = subprocess.Popen(self._cmd, shell=True,
                                              bufsize=1,
                                              # always use bash
                                              executable='/bin/bash',
                                              stdin=None,
                                              stdout=stdout,
                                              stderr=stderr)

                def reader(pipe, queue):
                    try:
                        with pipe:
                            for line in iter(pipe.readline, b''):
                                queue.put((pipe, line))
                    finally:
                        queue.put(None)

                import queue
                q = queue.Queue()

                nr_of_readers = 0
                if (self._output == 'both' or
                    self._output == 'stdout'):
                    nr_of_readers += 1
                    threading.Thread(target=reader, daemon=True, args=(self._proc.stdout, q)).start()
                if (self._output == 'both' or
                    self._output == 'stderr'):
                    nr_of_readers += 1
                    threading.Thread(target=reader, daemon=True, args=(self._proc.stderr, q)).start()

                for _ in range(nr_of_readers):
                    for source, line in iter(q.get, None):
                        l = line.decode()
                        if source == self._proc.stdout:
                            self.stdout.append(l.rstrip(os.linesep))
                            if (self._output == 'both' or
                                self._output == 'stdout'):
                                sys.stdout.write(self._prefix + l)
                        else:
                            self.stderr.append(l.rstrip(os.linesep))
                            if (self._output == 'both' or
                                self._output == 'stderr'):
                                sys.stderr.write(self._prefix + l)

                # can be caught anyway if a subprocess does not abide
                # to standard error codes
                if self._proc.wait() != 0:
                    raise self._owner.exc_class('error executing "{0}"'.format(self._cmd), self)

            finally:
                self._ret_val = self._proc.returncode
