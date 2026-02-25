# Error: duplicate enum member name
from enum import Enum

class Color(Enum):
    Red = 0
    Red = 1  # tpyc: error(/Duplicate enum member name/)
