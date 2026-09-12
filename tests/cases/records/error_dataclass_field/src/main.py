# Error: field() with both default and default_factory
from dataclasses import dataclass, field
from tpy import int32

@dataclass
class Bad:
    items: list[int32] = field(default_factory=list, default=0)  # tpyc: error(/cannot specify both/)

def main() -> None:
    pass

main()
