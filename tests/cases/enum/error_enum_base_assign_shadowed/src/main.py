# Enum base shadowed by assignment -- not recognized as enum
from enum import Enum, auto

Enum = 42

class Color(Enum):  # tpyc: error(/Unknown type/)
    Red = auto()
