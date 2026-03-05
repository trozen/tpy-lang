# Error: field() with positional arguments
from dataclasses import dataclass, field
from tpy import Int32

@dataclass
class Bad:
    value: Int32 = field(42)  # tpyc: error(/does not accept positional/)

def main() -> None:
    pass

main()
