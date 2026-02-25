# Error: mixed auto() and explicit values
from enum import Enum, auto

class Color(Enum):
    Red = auto()
    Green = 5  # tpyc: error(/Mixed auto/)
