# 3-level @dataclass inheritance chain: grandparent -> parent -> child
from dataclasses import dataclass
from tpy import Int32

@dataclass
class A:
    x: Int32

@dataclass
class B(A):
    y: Int32

@dataclass
class C(B):
    z: Int32

def main() -> None:
    c = C(1, 2, 3)
    print(c)
    print(c.x)
    print(c.y)
    print(c.z)
    print(c == C(1, 2, 3))
    print(c == C(1, 2, 4))
    # Intermediate works
    b = B(10, 20)
    print(b)
    print(b == B(10, 20))

main()
