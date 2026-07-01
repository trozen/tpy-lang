# An exposed enum crosses only as a function/method param or return, not as a
# getset field: the field path marshals via to_py/from_py, which have no enum
# overload. (A method returning the enum is the supported form.)
# tpy: ext_module
from enum import IntEnum
from tpy.extern import export


@export
class Color(IntEnum):
    RED = 1
    GREEN = 2


@export
class Holder:
    def __init__(self, c: Color):
        self.c = c  # tpyc: error(/exposed-enum getset field.*not supported/)
