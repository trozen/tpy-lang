# Error: field without default after field with default
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Bad:
    x: Int32 = 0
    y: Int32  # tpyc: error(/without default follows field with default/)
