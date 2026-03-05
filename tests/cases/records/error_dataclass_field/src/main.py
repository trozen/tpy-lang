# Error: field() with both default and default_factory
from dataclasses import dataclass, field
from tpy import Int32

@dataclass
class Bad:
    items: list[Int32] = field(default_factory=list, default=0)  # tpyc: error(/cannot specify both/)

def main() -> None:
    pass

main()
