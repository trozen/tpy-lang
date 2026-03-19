# Error: wrong type for keyword argument to @dataclass macro
from dataclasses import dataclass
from tpy import Int32

@dataclass(frozen="yes")
class Bad:  # tpyc: error(/'frozen' must be bool, got str/)
    x: Int32
