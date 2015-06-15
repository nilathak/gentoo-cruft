'''
provides a ui class which contains all user interface specific code.

- a logger based on the logging module
- a default parser based on the argparse module
- a custom exception handler to control traceback output (eg, during multithreading)
- a few stub functions for overloading in a more specific ui class
'''

import argparse
import logging
import sys

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
        self._owner = owner

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
        l = logging.INFO
        if self.args.verbosity > 1 or self.args.dry_run or self.args.traceback:
            l = logging.DEBUG
        elif self.args.verbosity > 0:
            l = ui.EXT_INFO

        # quiet switch takes precedence
        if self.args.quiet > 1:
            l = logging.ERROR
        elif self.args.quiet > 0:
            l = logging.WARNING
        self.logger.setLevel(l)

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
