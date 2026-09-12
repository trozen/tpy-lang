# Error: default_factory type doesn't match field type
from dataclasses import dataclass, field
from tpy import int32

@dataclass
class Bad:
    items: list[int32] = field(default_factory=dict)  # tpyc: error(/does not match field type/)

def main() -> None:
    pass

main()
