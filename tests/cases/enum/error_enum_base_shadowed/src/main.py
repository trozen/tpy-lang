# Enum base class shadowed by local def -- not recognized as enum
from enum import Enum, auto

def Enum() -> int:
    return 0

class Color(Enum):  # tpyc: error(/Unknown type/)
    Red = auto()
