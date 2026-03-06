# Error: non-frozen dataclass inheriting from frozen parent
from dataclasses import dataclass
from tpy import Int32

@dataclass(frozen=True)
class Frozen:
    x: Int32

@dataclass
class Bad(Frozen):  # tpyc: error(/Cannot inherit non-frozen.*from frozen/)
    y: Int32

def main() -> None:
    pass

main()
