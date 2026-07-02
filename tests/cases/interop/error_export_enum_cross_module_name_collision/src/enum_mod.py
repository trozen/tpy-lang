# tpy: ext_module
from enum import IntEnum
from tpy.extern import export


@export
class Color(IntEnum):
    RED = 1
    GREEN = 2
