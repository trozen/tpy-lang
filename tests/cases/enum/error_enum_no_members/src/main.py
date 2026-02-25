# Error: enum with no members
from enum import Enum

class Empty(Enum):  # tpyc: error(/must have at least one member/)
    pass
