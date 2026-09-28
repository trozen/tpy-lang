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

class Labeled:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

class Counted:
    count: int32 = 0

# multi-base: the synthesized __init__ calls neither base initializer, as in CPython;
# skipping Labeled's is the warned divergence (its field starts empty, not unset)
@dataclass
class Tagged(Labeled, Counted):  # tpyc: warning(/synthesized by '@dataclass' for 'Tagged' does not call 'Labeled.__init__'/)
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
    t = Tagged(5)
    t.label = "l"
    print("multi-base:", t.z, t.count, t.label)

main()
