# Error: field() with non-name default_factory (lambda)
from dataclasses import dataclass, field
from tpy import Int32

@dataclass
class Bad:
    items: list[Int32] = field(default_factory=lambda: [])  # tpyc: error(/default_factory must be a type name/)

def main() -> None:
    pass

main()
