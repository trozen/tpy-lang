# auto() shadowed by local def should not be treated as enum.auto()
from enum import Enum, auto

def auto() -> int:
    return 99

class T(Enum):
    A = auto()  # tpyc: error(/integer literal or auto/)
