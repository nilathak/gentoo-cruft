'''
provides fundamental classes to model the core control flow logic.

- a custom exception class for easier distinction of python and script errors
- a base class which serves as parent for all shell scripting module classes
  - it calls overloaded stub functions using a ui class handle
  - it uses a job class to dispatch python functions or subprocess commands
  - it dispatches jobs non-blocking for multithreaded operation
  - it provides a classmethod for flattening multidimensional lists
'''

import itertools
import math
import pylon.job as job
import pylon.ui as ui
import sys
import threading
import types

class script_error(Exception):
    'provide our own exception class for easy identification'

    @property
    def msg(self):
        return self._msg
    @property
    def owner(self):
        return self._owner

    def __init__(self, msg='No error info available', owner=None):
        self._msg = msg
        self._owner = owner

    def __str__(self):
        return self.msg

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
                 exc_class=script_error,
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

    def dispatch(self, cmd, output='both', passive=False, blocking=True):
        'dispatch a job (see job class for details)'

        job = self.job_class(ui=self.ui,
                             cmd=cmd,
                             output=output,
                             owner=self,
                             passive=passive,
                             blocking=blocking)
        if not blocking:
            # always keep a valid thread dependency tree
            parent = threading.current_thread()
            if parent not in self.jobs:
                self.jobs[parent] = list()
            self.jobs[parent].append(job)

        return job()

    def join(self):
        'join all known child threads, perform cleanup of job lists'
        if len(self.jobs) > 0:
            parent = threading.current_thread()

            to_join = self.jobs[parent]
            while any(map(lambda x: x.thread.is_alive(), to_join)):
                for j in to_join:
                    j.join()

                # find zombie parents (add children to current thread)
                [self.jobs[parent].extend(v) for (k, v) in self.jobs.items() if not k.is_alive()]
                to_join = self.jobs[parent]

            unhandled_exc = any(map(lambda x: x.exc_info != None, to_join))

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

    @classmethod
    def flatten(cls, l):
        for el in l:
            if hasattr(el, '__iter__') and not isinstance(el, str):
                for sub in cls.flatten(el):
                    yield sub
            else:
                yield el

    @staticmethod
    def unique_logspace(data_points, interval_range):
        exp = [x * math.log(interval_range)/data_points for x in range(0, data_points)]
        logspace = [int(round(math.exp(x))) for x in exp]
        for idx,val in enumerate(logspace):
            if idx > 0:
                if val <= new_val:
                    new_val = new_val + 1
                else:
                    new_val = val
            else:
                new_val = val
            yield new_val

    @staticmethod
    def chunk(n, iterable):
        it = iter(iterable)
        while True:
            chunk = tuple(itertools.islice(it, n))
            if not chunk:
                return
            yield chunk
