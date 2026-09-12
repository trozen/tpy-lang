# Error: field() with positional arguments
from dataclasses import dataclass, field
from tpy import int32

@dataclass
class Bad:
    value: int32 = field(42)  # tpyc: error(/does not accept positional/)

def main() -> None:
    pass

main()
