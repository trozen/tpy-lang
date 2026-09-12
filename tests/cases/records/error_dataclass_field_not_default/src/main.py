# Error: default_factory on a non-default-constructible type
from dataclasses import dataclass, field
from tpy import int32

class Rigid:
    value: int32
    def __init__(self, value: int32) -> None:
        self.value = value

@dataclass
class Bad:
    r: Rigid = field(default_factory=Rigid)  # tpyc: error(/not default-constructible/)

def main() -> None:
    pass

main()
