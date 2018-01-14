'''
Some basic functionality for python shell scripting.

Etymology: Greek pylOn, from pylE gate
    - a usually massive gateway
    - a monumental mass flanking an entranceway or an approach to a bridge
    - a tower for supporting either end of usually a number of wires over a long span
    - any of various towerlike structures

Refer to module docstrings for detailed explanations.
'''

import datetime
import functools
import itertools
import math

# =====================================================================================================================
# decorators
# =====================================================================================================================
def log_exec_time(func):
    'method decorator to explicitly log execution time'
    # keep original attributes for decorated function (eg, __name__, __doc__, ...)
    @functools.wraps(func)
    def __wrapper(self, *args, **kwargs):
        t1 = datetime.datetime.now()
        ret = func(self, *args, **kwargs)
        self.ui.info(func.__name__ + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')
        return ret
    return __wrapper

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

    def __init__(self, msg='No error info available', owner=None):
        self._msg = msg
        self._owner = owner

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
	
def flatten(l):
    'flatten multidimensional lists'
    for el in l:
        if hasattr(el, '__iter__') and not isinstance(el, str):
            for sub in flatten(el):
                yield sub
        else:
            yield el

def unique_logspace(data_points, interval_range):
    'provide logarithmically spaced integers in a certain range'
    data_points = min(data_points, interval_range)
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
