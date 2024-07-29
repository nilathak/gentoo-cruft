#!/usr/bin/env -S python3 -Wdefault
'''Search filesystem cruft on a gentoo system, dedicated to all OCD afflicted...

Inspired by ecatmur's cruft script:
http://forums.gentoo.org/viewtopic-t-152618-postdays-0-postorder-asc-start-0.html

- Ignore syntax
    ^/path/single_file$
    ^/path/single_dir/$
    ^/path/subtree$

- pattern/portage data is cached, system tree is always scanned.
    restrict system tree with -p option for faster debugging

====================================================================
FIXME
- concurrent execution of collect_* functions?
- how to exclude symlink without ignoring complete subtree? (eg, /usr/lib, /usr/local/lib)
- provide git-based ebuild in gentoo-overlay (dependencies: gentoolkit, pylon, python3)
- seperate pylon in own repo & provide git-based ebuild
- document how ignore patterns can exclude non-portage files AND
    portage files (eg, mask unavoidable md5 check fails due to eselect)
    option to list excluded portage files?
- create a usecase for a pattern file with ">=asdf-version" in its name
- gentoo forum post
        
====================================================================
'''

import functools
import hashlib
import gentoolkit.equery.check
import os
import pickle
import portage
import pprint
import re
import sys
import time
import pylon
import sys
import threading
import argparse
import logging
import email.mime.text
import getpass
import io
import smtplib
import socket

# ====================================================================
import subprocess

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
                                              # always use bash
                                              executable='/bin/bash',
                                              stdin=None,
                                              stdout=stdout,
                                              stderr=stderr)

                while self._proc.poll() == None:
                    (proc_stdout_b, proc_stderr_b) = self._proc.communicate(None)

                    if proc_stdout_b:
                        proc_stdout = proc_stdout_b.decode().rstrip(os.linesep).rsplit(os.linesep)
                        self.stdout.extend(proc_stdout)
                        if (self._output and
                            (self._output == 'both' or
                             self._output == 'stdout')):
                            [sys.stdout.write(self._prefix + l + os.linesep) for l in proc_stdout]

                    if proc_stderr_b:
                        proc_stderr = proc_stderr_b.decode().rstrip(os.linesep).rsplit(os.linesep)
                        self.stderr.extend(proc_stderr)
                        if (self._output and
                            (self._output == 'both' or
                             self._output == 'stderr')):
                            [sys.stderr.write(self._prefix + l + os.linesep) for l in proc_stderr]

                # can be caught anyway if a subprocess does not abide
                # to standard error codes
                if self._proc.returncode != 0:
                    raise self._owner.exc_class('error executing "{0}"'.format(self._cmd), self)

            finally:
                self._ret_val = self._proc.returncode
# ====================================================================
class ui(object):
    'nice command line user interface class used by pylon based scripts'
    EXT_INFO = logging.INFO - 1

    @property
    def args(self):
        return self._args
    @property
    def formatter(self):
        return self._formatter
    @property
    def logger(self):
        return self._logger
    @property
    def owner(self):
        return self._owner
    @property
    def parser(self):
        return self._parser

    def __init__(self, owner):
        self.__dict__.update({'_'+k:v for k,v in locals().items() if k != 'self'})

        # Logger
        ########################################
        # define additional logging level for a better verbosity granularity
        logging.addLevelName(ui.EXT_INFO, 'INFO')

        # set logger name to class name
        self._logger = logging.getLogger(self.owner.__class__.__name__)

        # define format of logger output
        fmt_str = '### %(name)s(%(asctime)s) %(levelname)s: %(message)s'
        self._formatter = {}
        self.formatter['default']  = logging.Formatter(fmt_str)
        self.formatter['threaded'] = logging.Formatter('%(threadName)s: ' + fmt_str)

        # add default handler for logging on stdout
        self._handler = {}
        self._handler['stdout'] = logging.StreamHandler(sys.stdout)
        self._handler['stdout'].setFormatter(self.formatter['default'])
        self.logger.addHandler(self._handler['stdout'])

        # Argument Parser
        ########################################
        # take any existing class doc string from our owner and set it as description
        self._parser = argparse.ArgumentParser(description=self.owner.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

        # define the common basic set of arguments
        self.parser.add_argument('--dry_run', action='store_true',
                                 help='switch to passive behavior (no subprocess execution)')
        self.parser.add_argument('-q', action='count', dest='quiet', default=0,
                                 help='quiet output (multiply for more silence)')
        self.parser.add_argument('--traceback', action='store_true',
                                 help='enable python traceback for debugging purposes')
        self.parser.add_argument('-v', action='count', dest='verbosity', default=0,
                                 help='verbose output (multiply for more verbosity)')

    def setup(self):
        self._args = self.parser.parse_args()

        # determine default verbosity behavior
        level = logging.INFO
        if self.args.verbosity > 1 or self.args.dry_run or self.args.traceback:
            level = logging.DEBUG
        elif self.args.verbosity > 0:
            level = ui.EXT_INFO

        # quiet switch takes precedence
        if self.args.quiet > 1:
            level = logging.ERROR
        elif self.args.quiet > 0:
            level = logging.WARNING
        self.logger.setLevel(level)

    def cleanup(self):
        'stub for basic cleanup stuff'
        pass

    def handle_exception_gracefully(self, et):
        'returns True if an exception should NOT be thrown at python interpreter'
        return (
            not self.args.traceback or

            # catch only objects deriving from Exception. Omit trivial
            # things like KeyboardInterrupt (derives from BaseException)
            not issubclass(et, Exception)
            )

    def excepthook(self, et, ei, tb):
        'pipe exceptions to logger, control traceback display. default exception handler will be replaced by this function'

        # switch to a more passive exception handling mechanism if
        # other threads are still active
        origin = 'default'
        if len(self.owner.jobs) > 0:
            origin = 'thread'

        if self.handle_exception_gracefully(et):
            self.error(repr(et) + ' ' + str(ei))
            if origin == 'default':
                self.cleanup()

                # generate error != 0
                sys.exit(1)

        else:
            if origin == 'thread':
                self.logger.exception('Traceback')
            else:
                # avoid losing any traceback info
                sys.__excepthook__(et, ei, tb)

    # logging level wrapper functions
    def debug(self, msg):
        self.logger.debug(msg)
    def error(self, msg):
        self.logger.error(msg)
    def ext_info(self, msg):
        self.logger.log(ui.EXT_INFO, msg)
    def info(self, msg):
        self.logger.info(msg)
    def warning(self, msg):
        self.logger.warning(msg)

                
# ====================================================================
class base(object):
    'common base for python scripts'

    @property
    def exc_class(self):
        return self._exc_class
    @property
    def job_class(self):
        return self._job_class
    @property
    def jobs(self):
        return self._jobs
    @property
    def ui(self):
        return self._ui
    @property
    def ui_class(self):
        return self._ui_class
    
    def __init__(self,
                 exc_class=pylon.script_error,
                 job_class=job,
                 ui_class=ui,
                 owner=None):
        self.__dict__.update({'_'+k:v for k,v in locals().items() if k != 'self'})
        if self._owner:
            self._exc_class = self._owner.exc_class
            self._job_class = self._owner.job_class

            # always reuse the interface of a calling script
            self._ui = self._owner.ui
        else:
            # delay ui creation until now, so 'self' is defined for
            # 'owner' option of ui
            self._ui = self.ui_class(self)
        self._jobs = {}

    def dispatch(self, cmd, blocking=True, **kwargs):
        'dispatch a job (see job class for details)'

        job = self.job_class(ui=self.ui,
                             owner=self,
                             cmd=cmd,
                             blocking=blocking,
                             **kwargs)
        if not blocking:
            # always keep a valid thread dependency tree
            parent = threading.current_thread()
            self.jobs.setdefault(parent, list()).append(job)
            
        return job()

    def join(self, **kwargs):
        'join all known child threads, perform cleanup of job lists'
        if len(self.jobs) > 0:
            parent = threading.current_thread()

            to_join = self.jobs[parent]
            while any(map(lambda x: x.thread.is_alive(), to_join)):
                for j in to_join:
                    j.join(**kwargs)

                # find zombie parents (add children to current thread)
                [self.jobs[parent].extend(v) for (k, v) in self.jobs.items() if not k.is_alive()]
                to_join = self.jobs[parent]

            unhandled_exc = any(map(lambda x: x.exc_info is not None, to_join))

            # all children finished
            del self.jobs[parent]

            if unhandled_exc:
                raise self.exc_class('unhandled exception in child thread(s)')

    def run(self):
        'common entry point for debugging and exception purposes'

        # install our custom exception handler
        sys.excepthook = self.ui.excepthook

        self.ui.setup()
        self.run_core()
        self.ui.cleanup()
# ====================================================================
class gentoo_job(job):
    report_stream_lock = threading.Semaphore()

    def exec_cmd(self):
        try:
            super().exec_cmd()
        finally:
            if self.ui.args.mail and not self.ui.args.dry_run:
                with gentoo_job.report_stream_lock:
                    if ((self._output == 'both' or
                        self._output == 'stdout') and
                        len(self._stdout) > 0):
                        self.ui.report_stream.write(self._prefix + (os.linesep + self._prefix).join(self._stdout) + os.linesep)
                    elif ((self._output == 'stderr') and
                          len(self._stderr) > 0):
                        self.ui.report_stream.write(self._prefix + (os.linesep + self._prefix).join(self._stderr) + os.linesep)
# ====================================================================
class gentoo_ui(ui):

    @property
    def fqdn(self):
        return self._fqdn
    @property
    def hostname(self):
        return self._hostname
    @property
    def report_stream(self):
        return self._report_stream

    def __init__(self, owner):
        super().__init__(owner)

        # add handler for mail logging
        self._report_stream = io.StringIO()
        self._handler['mail'] = logging.StreamHandler(self._report_stream)
        self._handler['mail'].setFormatter(self.formatter['default'])
        self.logger.addHandler(self._handler['mail'])

        # hooray, more emails (/etc/mail/aliases or ~/.forward needs to be set)...
        self._message_server = getpass.getuser() + '@localhost'

        self.parser.add_argument('--mail', action='store_true',
                                 help='generate additional mail report (def: <user>@localhost)')

        # when using operations:
        # - use self.parser_common from here on instead of self.parser
        # - do not forget to run init_op_parser after all parser_common statements in __init__
        self.parser_common = argparse.ArgumentParser(conflict_handler='resolve',
                                                     parents=[self.parser])

    def init_op_parser(self):
        # define operation subparsers with common options if class methods
        # with specific prefix are present
        ops_pattern = re.compile('^{0}_(.*)'.format(self._owner.__class__.__name__))
        ops = [x for x in map(ops_pattern.match, dir(self._owner)) if x is not None]
        if ops:
            subparsers = self.parser.add_subparsers(title='operations', dest='op')
            for op in ops:
                setattr(self, 'parser_' + op.group(1),
                        subparsers.add_parser(op.group(1),
                                              conflict_handler='resolve',
                                              parents=[self.parser_common],
                                              description=getattr(self._owner, op.string).__doc__,
                                              help=getattr(self._owner, op.string).__doc__))

    def setup(self):
        super().setup()

        self._hostname = socket.gethostname()
        self._fqdn = socket.getfqdn(self._hostname)

        self._report_subject = 'report'
        if hasattr(self.args, 'op'):
            self._report_subject = self.args.op

    def cleanup(self):
        'optionally send an email with all output to global message server'
        if (self.args.mail and
            not self.args.dry_run and
            len(self.report_stream.getvalue()) > 0):
            m = email.mime.text.MIMEText(self.report_stream.getvalue())
            m['From'] = self._owner.__class__.__name__ + '@' + self.fqdn
            m['To'] = self._message_server
            m['Subject'] = self._report_subject
            s = smtplib.SMTP(self._message_server.split('@')[1])
            s.set_debuglevel(0)
            self.debug('Sending mail...')
            s.sendmail(m['From'], m['To'], m.as_string())
            s.quit()
# ====================================================================


# FIXME configurability (use TOML? https://docs.python.org/3/library/tomllib.html#module-tomllib)
cache_base_path = '/tmp'
cache_base_name = 'cruft_cache'
comment_char = '#'
default_pattern_root = '/usr/bin/cruft.d'

gtk_check = gentoolkit.equery.check.VerifyContents()
trees = portage.create_trees()
vardb = trees[portage.settings['EROOT']]["vartree"].dbapi
vardb_path = os.path.join(portage.settings['EROOT'], portage.const.VDB_PATH)

# portage vartree dict indices
po_type = 0
po_timestamp = 1
po_digest = 2

# cruft dict indices
co_date = 0

class ui(gentoo_ui):
    def __init__(self, owner):
        super().__init__(owner)
        self.parser_common.add_argument('-i', '--pattern_root',
                                        default=default_pattern_root,
                                        help='give alternative path to directory containing ignore pattern files')
        self.init_op_parser()
        self.parser_report.add_argument('-c', '--check', action='store_true',
                                        help='perform gentoolkit sanity checks on all installed packages (time consuming!)')
        self.parser_report.add_argument('-p', '--path',
                                        default='/',
                                        help='check only specific path for cruft')
        self.parser_report.add_argument('-f', '--format', choices=('path', 'date', 'rm_chain'),
                                        default='path',
                                        help='date: report cruft objects sorted by modification date,\
                                        path: report cruft objects sorted by object path (default),\
                                        rm_chain: report cruft objects as chained rm commands')
        
    def setup(self):
        super().setup()
        if not self.args.op:
            self.parser.print_help()
            raise self.owner.exc_class('Specify at least one subcommand operation')
        
class cruft(base):
    __doc__ = sys.modules[__name__].__doc__
    
    def run_core(self):
        # FIXME use self._data ?
        self.data = dict()
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()

    @functools.lru_cache(typed=True)
    def ignored(self, path):
        return self.data['patterns']['single_regex'].match(path)
        
    @functools.lru_cache(typed=True)
    def collect_ignore_patterns(self):
        self.ui.info('Collecting ignore patterns...')

        pattern_files = list()
        for root, dirs, files in os.walk(self.ui.args.pattern_root):
            for f in files:
                # assume leaf dirs contain package-specific patterns
                if not dirs:
                    # check if any version of the package is installed
                    pkg = os.path.join(os.path.basename(root), f)
                    # working examples:
                    # vardb.match('net-p2p/go-ethereum')
                    # vardb.match('net-p2p/go-ethereum-1.5.5')
                    # vardb.match('net-p2p/go-ethereum[opencl]')
                    if not vardb.match(pkg):
                        self.ui.ext_info('Not installed: ' + pkg)
                        continue
                    self.ui.ext_info('Installed: ' + pkg)
                pattern_files.append(os.path.join(root, f))

        re_map = dict()

        for pattern_file in pattern_files:
            self.ui.ext_info('Extracting patterns from: ' + pattern_file)
            
            # either we generate regexes from executable scripts, ...
            re_list_raw = list()
            if os.access(pattern_file, os.X_OK):
                try:
                    re_list_raw = self.dispatch(pattern_file,
                                                output=None).stdout
                except self.exc_class:
                    self.ui.error('Script failed: ' + pattern_file)

            # ... or we simply read in lines from a text file
            else:
                with open(pattern_file, 'r') as f:
                    for line in f:
                        # ignore comment lines
                        comment_idx = line.find(comment_char)
                        line_no_comments = line
                        if comment_idx != -1:
                            line_no_comments = line[:comment_idx]
                        re_list_raw.append(line_no_comments)

            # - strip all metachars
            # - interpret spaces as delimiter for multiple patterns
            #   on one line. needed for automatic bash expansion by
            #   {}. however this breaks ignore patterns with spaces!
            re_list_of_file = pylon.flatten(x.removesuffix(os.linesep).strip().split() for x in re_list_raw)

            # pattern sanity checks, to facilitate pattern file debugging
            for regex in re_list_of_file:
                try:
                    re.compile(regex)
                except Exception:
                    self.ui.error(f'Skipped invalid expression in {pattern_file} ({regex})')
                else:
                    # even if patterns are listed redundantly in one file, just add it once
                    re_map.setdefault(regex, set()).add(pattern_file)

        self.ui.debug('Compiling all expressions into one long regex...')
        re_single_regex = re.compile('|'.join(re_map.keys()))

        return {'map': re_map,
                'single_regex': re_single_regex}
        
    @functools.lru_cache(typed=True)
    def collect_portage_objects(self):
        if 'patterns' not in self.data: self.data['patterns'] = self.collect_ignore_patterns()

        self.ui.info('Collecting objects managed by portage...')
        objects = set()
        for pkg in sorted(vardb.cpv_all()):
            contents = vardb._dblink(pkg).getcontents()

            check = dict()
            for k,v in contents.items():

                # just flatten out the dirname part to avoid tinkering with symlinks introduced by portage itself.
                k = os.path.join(os.path.realpath(os.path.dirname(k)),
                                 os.path.basename(k))
                
                # add trailing slashes to directories for easier regex matching
                if v[po_type] == 'dir':
                    k += '/'
                    
                objects.add(k)
                check[k] = v

            # implicitly checks for missing portage objects
            if self.ui.args.check:
                (n_passed, n_checked, errs) = gtk_check._run_checks(check)
                for err in errs:
                    path = err.split()[0]
                    if not self.ignored(path) and path.startswith(self.ui.args.path):
                        self.ui.error(pkg + ': ' + err)
                
        return objects

    @functools.lru_cache(typed=True)
    def collect_system_objects(self):
        if 'patterns' not in self.data: self.data['patterns'] = self.collect_ignore_patterns()

        self.ui.info('Collecting objects in system tree...')
        objects = set()
        for root, dirs, files in os.walk(self.ui.args.path, followlinks=False, onerror=lambda x: self.ui.error(str(x))):

            for d in list(dirs):
                path = os.path.join(root, d)

                # handle ignored directory symlinks as files
                if os.path.islink(path):
                    dirs.remove(d)
                    files.append(d)
                    continue

                # remove excluded subtrees early to speed up walk (eg, user data)
                # leave dir without slash in objects => filtered by this regex anyway
                if self.ignored(path):
                    dirs.remove(d)
                    objects.add(path)
                    continue

                # add a trailing slash to allow easy distinction between subtree and single dir exclusion
                objects.add(path + '/')
            
            for f in files:
                path = os.path.join(root, f)
                objects.add(path)

                # report broken symlinks but keep them in list (needed for portage - system report)
                if not os.path.exists(path):
                    self.ui.error('Broken symlink detected: ' + path)

        return objects

    def collect_cruft_objects(self):
        if 'patterns' not in self.data: self.data['patterns'] = self.collect_ignore_patterns()
        if 'portage' not in self.data: self.data['portage'] = self.collect_portage_objects()
        if 'system' not in self.data: self.data['system'] = self.collect_system_objects()

        self.ui.info('Identifying cruft...')
        self.ui.debug('Generating difference set (system - portage)...')
        cruft = self.data['system'] - self.data['portage']

        self.ui.debug('Applying ignore patterns on (system - portage)...')
        remaining = {path for path in cruft if not self.ignored(path)}

        self.ui.debug('Removing parent directories of already ignored paths...')
        ignored = cruft - remaining
        for path in ignored:
            remaining = [x for x in remaining if not path.startswith(x) or x[-1] != '/']

        # FIXME use self._n_ignored ?
        self.n_ignored = len(cruft) - len(remaining)

        # add a date info to the remaining objects
        cruft = dict()
        remaining.sort()
        for path in remaining:
            try:
                cruft[path] = [time.localtime(os.lstat(path).st_mtime)]
            except OSError:
                self.ui.error('Path disappeared: ' + path)

        return cruft

    def collect_cached_data(self):
        self.ui.debug('Collecting data and using cache when possible...')
        
        cache_path = os.path.join(cache_base_path, cache_base_name + '_' + self.ui.hostname)
        dirty = False
        if os.access(cache_path, os.R_OK):
            with open(cache_path, 'rb') as cache_file:
                self.ui.info(f'Loading cache {cache_path}...')
                self.data = pickle.load(cache_file)

        # determine portage dir state
        portage_state = hashlib.md5(str(os.stat(vardb_path)).encode('utf-8')).hexdigest()

        # determine pattern dir state
        patterns_state = ''
        for root, dirs, files in os.walk(self.ui.args.pattern_root):
            for f in files:
                patterns_state += hashlib.md5(str(os.stat(os.path.join(root, f))).encode('utf-8')).hexdigest()
        patterns_state = hashlib.md5(patterns_state.encode('utf-8')).hexdigest()
        
        if ('portage' not in self.data or
            'portage_state' not in self.data or
            self.data['portage_state'] != portage_state or
            self.ui.args.check):
          
            # portage changes can affect patterns (deriving patterns from portage API calls),
            # thus collect portage first, which implicitely collects patterns.
            self.data.pop('patterns', None)
            self.data.pop('patterns_state', None)
            self.data['portage'] = self.collect_portage_objects()
            self.data['portage_state'] = portage_state
            dirty = True
        else:
            self.ui.warning('No portage changes detected => reusing cache...')
            
        if ('patterns' not in self.data or
            'patterns_state' not in self.data or
            self.data['patterns_state'] != patterns_state):
          
            self.data['patterns'] = self.collect_ignore_patterns()
            self.data['patterns_state'] = patterns_state
            dirty = True
        else:
            self.ui.warning('No pattern file changes detected => reusing cache...')
           
        if dirty:
            with open(cache_path, 'wb') as cache_file:
                self.ui.info('Storing cache...')
                pickle.dump(self.data, cache_file)

    def cruft_report(self):
        '''
        identify potential cruft objects on your system
        '''
        self.collect_cached_data()
        # FIXME use self._cruft_dict ?
        self.cruft_dict = self.collect_cruft_objects()

        if self.cruft_dict:
            cruft_keys = list(self.cruft_dict.keys())

            # useful sort keys
            path = lambda x: x
            date = lambda x: self.cruft_dict[x][co_date]
            path_str = lambda x: path(x)
            date_str = lambda x: time.asctime(date(x))

            # sort & format according to option
            fmt = '{path_str}, {date_str}'
            reverse = False
            sort_key = path
            if self.ui.args.format == 'date':
                reverse = True
                sort_key = date
            if self.ui.args.format == 'rm_chain':
                fmt = 'rm -rf "{path_str}" && \\'
            cruft_keys.sort(key=sort_key, reverse=reverse)

            self.ui.info('Cruft objects:' + os.linesep +
                         os.linesep.join(
                             [fmt.format(path_str=path_str(co),
                                         date_str=date_str(co))
                              for co in cruft_keys]))
            self.ui.warning(f'Cruft objects identified: {len(cruft_keys)}')

        self.ui.info(f'Cruft files ignored: {self.n_ignored}')

    def cruft_list(self):
        '''
        list ignore patterns and their origin + do some sanity checking
        '''

        # FIXME check idea: ignored files which have not been updated in a while => potentially incorrect ignore pattern?
        # FIXME check idea: determine nr of files in excluded subtrees => list largest ones
        # FIXME check idea: list pattern files for packages which are not installed => delete, or keep for larger user base?
        
        # re-using functions from report op requires sane args defaults
        self.ui.args.check = False
        self.ui.args.path = '/'

        self.collect_cached_data()
        if 'system' not in self.data: self.data['system'] = self.collect_system_objects()

        # FIXME put this verbose info into a separate operation
        self.ui.info('List of patterns and the files which generated them:')
        pprint.pprint(self.data['patterns']['map'])
        
        # do some sanity checking
        self.ui.info('Identical patterns are listed in multiple files:')
        pprint.pprint({k:v for k,v in self.data['patterns']['map'].items() if len(v) != 1})

        # FIXME multiprocessing? takes too long, output too verbose
        # FIXME try to match with single_regex first, if match => iterate through every pattern
        self.ui.info('Redundant ignore patterns (remove from pattern file, or leave it to mask MD5 fails):')
        for k,v in sorted(self.data['patterns']['map'].items()):
            matched = False
            pattern = re.compile(k)
            for path in self.data['portage']:
                if pattern.match(path):
                    matched = True
                    break
            if matched:
                pprint.pprint({k:v})
                
        # FIXME multiprocessing? takes too long, output too verbose
        self.ui.info('Non-matching patterns (be patient!):')
        for k,v in sorted(self.data['patterns']['map'].items()):
            matched = False
            pattern = re.compile(k)
            for path in self.data['system']:
                if pattern.match(path):
                    matched = True
                    break
            if not matched:
                pprint.pprint({k:v})
                
if __name__ == '__main__':
    app = cruft(job_class=gentoo_job,
                ui_class=ui)
    app.run()
    #import cProfile
    #try:
    #   cProfile.run('app.run()', '/tmp/fooprof')
    #except:
    #   ...
    #import pstats
    #p = pstats.Stats('/tmp/fooprof')
    #p.sort_stats('cumulative').print_stats(30)
    #p.sort_stats('time').print_stats(30)
