# An inherited __post_init__ fires exactly once, via the super().__init__ chain.
# The child must not re-append its own call for an inherited hook, or the hook
# would run twice under TPy's static dispatch (CPython runs it once).
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Base:
    a: Int32

    def __post_init__(self) -> None:
        print("post_init")

@dataclass
class Child(Base):
    b: Int32

def main() -> None:
    c = Child(1, 2)
    print(c.a)
    print(c.b)  # "post_init" printed once above, not twice

main()
