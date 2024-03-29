'''
provides fundamental classes to model the core control flow logic.

- a base class which serves as parent for all shell scripting module classes
  - it calls overloaded stub functions using a ui class handle
  - it uses a job class to dispatch python functions or subprocess commands
  - it dispatches jobs non-blocking for multithreaded operation
'''

import pylon.job
import pylon.ui
import sys
import threading

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
                 job_class=pylon.job.job,
                 ui_class=pylon.ui.ui,
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
