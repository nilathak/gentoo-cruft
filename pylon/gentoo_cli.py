'''
a command-line task dispatcher with subcommand parser
- includes 'subcommand' decorator to register methods as argparse subcommand entry point
- optionally collects all output for smtp mail reporting (default address: <user>@localhost)
'''

import argparse
import asyncio
import email.mime.text
import getpass
import io
import logging
import pylon
import smtplib
import socket
import sys

# =====================================================================================================================
# decorators
# =====================================================================================================================
def subcommand(func):
    'decorator to register argparse subcommand'
    func.subcommand = True
    return func

# =====================================================================================================================
# classes
# =====================================================================================================================
class tee_stringio(io.StringIO):
    def __init__(self, _stream0, _stream1):
        super().__init__()
        self.__dict__.update(locals())
    
    def write(self, s):
        self._stream0.write(s)
        self._stream1.write(s)

class gentoo_cli(pylon.base_cli.base_cli):
    __doc__ = sys.modules[__name__].__doc__

    @property
    def hostname(self):
        return self._hostname
    
    def __init__(self):
        super().__init__()

        self._hostname = socket.gethostname()
        
        # hooray, more emails (/etc/mail/aliases or ~/.forward needs to be set)...
        self._message_server = getpass.getuser() + '@localhost'
         
        self.parser.add_argument('--mail', action='store_true',
                                 help='generate additional mail report (def: <user>@localhost)')
         
        # when using subcommands:
        # - use self.parser_common from here on instead of self.parser
        # - do not forget to run init_subcommands after all parser_common statements in __init__
        self.parser_common = argparse.ArgumentParser(conflict_handler='resolve',
                                                     parents=[self.parser])
        
    def init_subcommands(self):
        for idx,subcommand in enumerate(x for x in (getattr(self, x) for x in dir(self)) if hasattr(x, 'subcommand')):
            if idx == 0:
                subparsers = self.parser.add_subparsers(title='subcommands', dest='subcommand', required=True)
            setattr(self, 'parser_' + subcommand.__name__,
                    subparsers.add_parser(subcommand.__name__,
                                          conflict_handler='resolve',
                                          parents=[self.parser_common],
                                          description=subcommand.__doc__,
                                          help=subcommand.__doc__))

    async def setup(self):
        await super().setup()

        if (self.args.mail):

            # add handler for mail logging
            self._mail_stream = io.StringIO()
            handler = logging.StreamHandler(self._mail_stream)
            handler.setFormatter(self._formatter)
            self._logger.addHandler(handler)
            
            sys.stdout = tee_stringio(sys.stdout, pylon.base_cli.prefixed_stringio(self._mail_stream))
            sys.stderr = tee_stringio(sys.stderr, pylon.base_cli.prefixed_stringio(self._mail_stream))
        
    async def cleanup(self):
        await super().cleanup()

        if (getattr(self.args, 'mail', False) and
            len(self._mail_stream.getvalue()) > 0):
            m = email.mime.text.MIMEText(self._mail_stream.getvalue())
            m['From'] = self.__class__.__name__ + '@' + socket.getfqdn(self._hostname)
            m['To'] = self._message_server
            m['Subject'] = getattr(self.args, 'subcommand', self.__class__.__name__)
            s = smtplib.SMTP(self._message_server.split('@')[1])
            s.set_debuglevel(0)
            self.logger.debug('Sending mail...')
            s.sendmail(m['From'], m['To'], m.as_string())
            s.quit()

    async def run_core(self):
        await getattr(self, self.args.subcommand)()
    
