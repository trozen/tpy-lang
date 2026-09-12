# Error: unknown keyword argument to @dataclass macro
from dataclasses import dataclass
from tpy import int32

@dataclass(frozen=True, slots=True)
class Bad:  # tpyc: error(/unknown keyword argument 'slots'/)
    x: int32
