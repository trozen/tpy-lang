# IntEnum base shadowed by local def -- not recognized as IntEnum
from enum import IntEnum

def IntEnum() -> int:
    return 0

class Priority(IntEnum):  # tpyc: error(/Unknown type/)
    Low = 1
