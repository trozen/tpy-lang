# Error: parent default field followed by child non-default field
from dataclasses import dataclass
from tpy import int32

@dataclass
class Base:
    x: int32
    y: int32 = 0

@dataclass
class Bad(Base):
    z: int32  # tpyc: error(/without default follows field with default/)

def main() -> None:
    pass

main()
