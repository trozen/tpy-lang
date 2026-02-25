# Error: duplicate enum value
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 0  # tpyc: error(/Duplicate enum value/)
