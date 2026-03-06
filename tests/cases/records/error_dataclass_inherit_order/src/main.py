# Error: parent default field followed by child non-default field
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Base:
    x: Int32
    y: Int32 = 0

@dataclass
class Bad(Base):
    z: Int32  # tpyc: error(/without default follows field with default/)

def main() -> None:
    pass

main()
