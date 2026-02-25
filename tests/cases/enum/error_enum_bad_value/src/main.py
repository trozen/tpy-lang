# Error: non-integer enum value
from enum import Enum

class Color(Enum):
    Red = "red"  # tpyc: error(/integer literal or auto/)
