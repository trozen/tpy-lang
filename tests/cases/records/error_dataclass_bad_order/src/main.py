# Error: field without default after field with default
from dataclasses import dataclass
from tpy import int32

@dataclass
class Bad:
    x: int32 = 0
    y: int32  # tpyc: error(/without default follows field with default/)
