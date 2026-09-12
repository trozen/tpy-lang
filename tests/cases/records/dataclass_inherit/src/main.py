# @dataclass inheritance: child includes parent fields in __init__ and __eq__
from dataclasses import dataclass
from tpy import int32

@dataclass
class Base:
    x: int32
    y: int32

@dataclass
class Child(Base):
    z: int32

def main() -> None:
    c = Child(1, 2, 3)
    print(c.x)
    print(c.y)
    print(c.z)
    print(c)
    # Equality compares all fields (parent + child)
    print(c == Child(1, 2, 3))
    print(c == Child(1, 2, 4))
    print(c == Child(9, 9, 3))
    # Keyword args
    c2 = Child(x=1, y=2, z=3)
    print(c == c2)
    # Parent works independently
    b = Base(1, 2)
    print(b)
    print(b == Base(1, 2))

main()
