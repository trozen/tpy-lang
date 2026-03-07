# positional patterns on inherited @dataclass (parent + own fields)
from dataclasses import dataclass

@dataclass
class Base:
    x: float
    y: float

@dataclass
class Child(Base):
    z: float

@dataclass
class Other:
    v: float

def describe(s: Child | Other) -> None:
    match s:
        case Child(a, b, c):
            print(a + b + c)
        case Other(v):
            print(v)

def main() -> None:
    obj: Child | Other = Child(1.0, 2.0, 3.0)
    describe(obj)
    o: Child | Other = Other(9.0)
    describe(o)

main()
