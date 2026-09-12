# Error: frozen dataclass inheriting from non-frozen (or vice versa)
from dataclasses import dataclass
from tpy import int32

@dataclass
class Base:
    x: int32

@dataclass(frozen=True)
class Bad(Base):  # tpyc: error(/Cannot inherit frozen.*from non-frozen/)
    y: int32

def main() -> None:
    pass

main()
