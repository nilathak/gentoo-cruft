'''
Some basic functionality for python shell scripting.

Etymology: Greek pylOn, from pylE gate
    - a usually massive gateway
    - a monumental mass flanking an entranceway or an approach to a bridge
    - a tower for supporting either end of usually a number of wires over a long span
    - any of various towerlike structures

Refer to module docstrings for detailed explanations.
'''

import asyncio
import datetime
import fcntl
import functools
import inspect
import itertools
import math
import os

# =====================================================================================================================
# context managers
# =====================================================================================================================
class path_lock():
    def __init__(self, _path):
        self.__dict__.update(locals())
        assert(os.path.exists(_path))

    def __enter__(self):
        self._fd = os.open(self._path, os.O_RDONLY)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Exception(f'path is locked by another process: {self._path}') from None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)

# =====================================================================================================================
# decorators
# =====================================================================================================================
def log_exec_time(func):
    'method decorator to explicitly log execution time'

    # keep original attributes for decorated function (eg, __name__, __doc__, ...)
    @functools.wraps(func)
    def method_wrapper(self, *args, **kwargs):
        t1 = datetime.datetime.now()
        ret = func(self, *args, **kwargs)
        # FIXME remove old style pylon log function after complete migration
        self.ui.info(func.__name__ + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')
        return ret

    @functools.wraps(func)
    async def async_method_wrapper(self, *args, **kwargs):
        t1 = datetime.datetime.now()
        ret = await func(self, *args, **kwargs)
        self.logger.info(func.__name__ + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')
        return ret

    if inspect.iscoroutinefunction(func):
        return async_method_wrapper
    return method_wrapper

# =====================================================================================================================
# exceptions
# =====================================================================================================================
class script_error(Exception):
    'provide our own exception class for easy identification'

    @property
    def msg(self):
        return self._msg
    @property
    def owner(self):
        return self._owner

    def __init__(self, _msg='No error info available', _owner=None):
        super().__init__()
        self.__dict__.update(locals())

    def __str__(self):
        return self.msg

# =====================================================================================================================
# generators
# =====================================================================================================================
def chunk(n, iterable):
    it = iter(iterable)
    while True:
        chunk = tuple(itertools.islice(it, n))
        if not chunk:
            return
        yield chunk
	
def flatten(iterable):
    'flatten multidimensional lists'
    for elem in iterable:
        if hasattr(elem, '__iter__') and not isinstance(elem, str):
            for sub_elem in flatten(elem):
                yield sub_elem
        else:
            yield elem

def unique_logspace(data_points, interval_range):
    'provide logarithmically spaced integers in a certain range'
    data_points = min(data_points, interval_range)
    exp = [x * math.log(interval_range)/data_points for x in range(0, data_points)]
    logspace = [int(round(math.exp(x))) for x in exp]
    for idx,val in enumerate(logspace):
        if val <= (idx + 1):
            yield idx + 1
        else:
            yield val
