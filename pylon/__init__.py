'''
Some basic functionality for python scripting.

Etymology: Greek pylOn, from pylE gate
    - a usually massive gateway
    - a monumental mass flanking an entranceway or an approach to a bridge
    - a tower for supporting either end of usually a number of wires over a long span
    - any of various towerlike structures
'''

import itertools
import math
import os
# import submodules to simplify pylon import statement in user script
import pylon.base_cli
import pylon.gentoo_cli

# =====================================================================================================================
# context managers
# =====================================================================================================================
class path_lock():
    def __init__(self, _path):
        self.__dict__.update(locals())

    def __enter__(self):
        try:
            os.makedirs(self._path)
        except OSError:
            raise Exception(f'path is already locked: {self._path}')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        os.rmdir(self._path)

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
