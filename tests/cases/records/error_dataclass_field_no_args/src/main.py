# Error: field() with no arguments
from dataclasses import dataclass, field
from tpy import int32

@dataclass
class Bad:
    value: int32 = field()  # tpyc: error(/requires 'default' or 'default_factory'/)

def main() -> None:
    pass

main()
