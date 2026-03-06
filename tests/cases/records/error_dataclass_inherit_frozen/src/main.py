# Error: frozen dataclass inheriting from non-frozen (or vice versa)
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Base:
    x: Int32

@dataclass(frozen=True)
class Bad(Base):  # tpyc: error(/Cannot inherit frozen.*from non-frozen/)
    y: Int32

def main() -> None:
    pass

main()
