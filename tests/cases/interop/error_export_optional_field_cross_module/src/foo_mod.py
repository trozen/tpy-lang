# tpy: ext_module
from enum import IntEnum
from tpy.extern import export


@export
class Hue(IntEnum):
    RED = 1
    BLUE = 2
