# @dataclass(order=True) inheritance: comparison uses parent + child fields
from dataclasses import dataclass
from tpy import Int32

@dataclass(order=True)
class Base:
    x: Int32
    y: Int32

@dataclass(order=True)
class Child(Base):
    z: Int32

def main() -> None:
    a = Child(1, 2, 3)
    b = Child(1, 2, 4)
    c = Child(2, 0, 0)
    # z differs: 3 < 4
    print(a < b)
    print(b < a)
    # x differs: 1 < 2
    print(a < c)
    print(c < a)
    # Equal
    print(a <= Child(1, 2, 3))
    print(a >= Child(1, 2, 3))
    print(a)

main()
