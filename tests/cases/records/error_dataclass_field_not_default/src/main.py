# Error: default_factory on a non-default-constructible type
from dataclasses import dataclass, field
from tpy import Int32

class Rigid:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value

@dataclass
class Bad:
    r: Rigid = field(default_factory=Rigid)  # tpyc: error(/not default-constructible/)

def main() -> None:
    pass

main()
