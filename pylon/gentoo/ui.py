'''
provides a ui class which extends the pylon ui class.

- a new logger for smtp mail reporting (default address: <user>@localhost)
- member functions of base subclass using a naming scheme like <class>_<subparser>
  are automatically used for argparse subparser definition
'''

import argparse
import email.mime.text
import getpass
import io
import logging
import pylon.ui
import re
import smtplib
import socket

class ui(pylon.ui.ui):

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
