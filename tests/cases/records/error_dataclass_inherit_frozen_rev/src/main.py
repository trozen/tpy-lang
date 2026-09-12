# Error: non-frozen dataclass inheriting from frozen parent
from dataclasses import dataclass
from tpy import int32

@dataclass(frozen=True)
class Frozen:
    x: int32

@dataclass
class Bad(Frozen):  # tpyc: error(/Cannot inherit non-frozen.*from frozen/)
    y: int32

def main() -> None:
    pass

main()
