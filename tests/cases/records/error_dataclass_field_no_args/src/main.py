# Error: field() with no arguments
from dataclasses import dataclass, field
from tpy import Int32

@dataclass
class Bad:
    value: Int32 = field()  # tpyc: error(/requires 'default' or 'default_factory'/)

def main() -> None:
    pass

main()
