# Module prefix shadowed -- enum.Enum and enum.auto() not resolved
import enum

def enum() -> int:
    return 0

class Color(enum.Enum):  # tpyc: error(/Unsupported qualified type/)
    Red = enum.auto()
