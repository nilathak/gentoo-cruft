'''
provides a ui class which extends the pylon ui class.

- a new logger for smtp mail reporting (default address: root@localhost)
- member functions of base subclass using a naming scheme like <class>_<subparser>
  are automatically used for argparse subparser definition
'''

import argparse
import datetime
import email.mime.text
import functools
import io
import logging
import os
import pylon.ui as ui
import re
import smtplib
import socket

class ui(ui.ui):

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

        # hooray, more emails (alias needs to be set)...
        self._message_server = 'root@localhost'

        self.parser.add_argument('--mail', action='store_true',
                                 help='generate additional mail report (def: root@localhost)')

        # when using operations:
        # - use self.parser_common from here on instead of self.parser
        # - do not forget to run init_op_parser after all parser_common statements in __init__
        self.parser_common = argparse.ArgumentParser(conflict_handler='resolve',
                                                     parents=[self.parser])

    def init_op_parser(self):
        # define operation subparsers with common options if class methods
        # with specific prefix are present
        ops_pattern = re.compile('^{0}_(.*)'.format(self._owner.__class__.__name__))
        ops = [x for x in map(ops_pattern.match, dir(self._owner)) if x != None]
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
        'send optional email with all output to global message server'
        if (self.args.mail and
            not self.args.dry_run and
            len(self.report_stream.getvalue()) > 0):
            m = email.mime.text.MIMEText(self.report_stream.getvalue())
            m['From'] = self._owner.__class__.__name__ + '@' + self.fqdn
            m['To'] = self._message_server
            m['Subject'] = self._report_subject
            s = smtplib.SMTP(self._message_server.split('@')[1])
            s.set_debuglevel(0)
            s.sendmail(m['From'], m['To'], m.as_string())
            s.quit()

    # method decorator to explicitly log execution time
    def log_exec_time(func):
        # keep original attributes for decorated function (eg, __name__, __doc__, ...)
        @functools.wraps(func)
        def __wrapper(self, *args, **kwargs):
            t1 = datetime.datetime.now()
            ret = func(self, *args, **kwargs)
            self.ui.info(func.__name__ + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')
            return ret
        return __wrapper
