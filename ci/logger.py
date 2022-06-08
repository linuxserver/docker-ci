#!/usr/bin/env python3

import os
import logging
from logging.handlers import TimedRotatingFileHandler
import re
import platform


logger = logging.getLogger()

class CustomLogFormatter(logging.Formatter):
    """Formatter that removes creds from logs."""
    ACCESS_KEY = os.environ.get("ACCESS_KEY","super_secret_key")
    SECRET_KEY = os.environ.get("SECRET_KEY","super_secret_key")

    def formatException(self, exc_info):
        """Format an exception so that it prints on a single line."""
        result = super(CustomLogFormatter, self).formatException(exc_info)
        return repr(result)  # or format into one line however you want to

    def format_credential_key(self, s):
        return re.sub(self.ACCESS_KEY, '(removed)', s)

    def format_secret_key(self, s):
        return re.sub(self.SECRET_KEY, '(removed)', s)

    def format(self, record):
        s = super(CustomLogFormatter, self).format(record)
        if record.exc_text:
            s = s.replace('\n', '') + '|'
        s = self.format_credential_key(s)
        s = self.format_secret_key(s)

        return s


def configure_logging(log_level:str):
    """Setup console and file logging"""

    logger.handlers = []
    logger.setLevel(log_level)

    # Console logging
    ch = logging.StreamHandler()
    cf = CustomLogFormatter('%(asctime)-15s | (%(threadName)-9s) %(name)-43s | %(levelname)-8s | (%(module)s.%(funcName)s|line:%(lineno)d) | %(message)s |', '%d/%m/%Y %H:%M:%S')
    ch.setFormatter(cf)
    ch.setLevel(log_level)
    logger.addHandler(ch)

    # File logging
    fh = TimedRotatingFileHandler(os.path.join(os.getcwd(),'debug.log'), when="midnight", interval=1, backupCount=7, delay=True, encoding='utf-8')
    f = CustomLogFormatter('%(asctime)-15s | (%(threadName)-9s) %(name)-43s | %(levelname)-8s | (%(module)s.%(funcName)s|line:%(lineno)d) | %(message)s |', '%d/%m/%Y %H:%M:%S')
    fh.setFormatter(f)
    fh.setLevel(log_level)
    logger.addHandler(fh)
    

    if log_level.upper() == "DEBUG":
        logging.getLogger("spam").setLevel(logging.DEBUG) # Change external loggers to debug if necessary
        logging.debug('Operating system: %s', platform.platform())
        logging.debug('Python version: %s', platform.python_version())
    else:
        logging.getLogger("ham").setLevel(logging.CRITICAL) # Set external loggers to a level if necessary
