# Error: default_factory type doesn't match field type
from dataclasses import dataclass, field
from tpy import Int32

@dataclass
class Bad:
    items: list[Int32] = field(default_factory=dict)  # tpyc: error(/does not match field type/)

def main() -> None:
    pass

main()
