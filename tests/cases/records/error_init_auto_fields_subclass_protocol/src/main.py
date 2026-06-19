# An inferred subclass field gets the same validation as a declared one:
# a protocol-typed field is rejected (else codegen emits ill-formed C++).
from typing import Protocol

class Drawable(Protocol):
    def area(self) -> int: ...

class Base:
    pass

class Sub(Base):
    def __init__(self, d: Drawable):
        self.d = d  # tpyc: error(/Protocol type 'Drawable' cannot be used as a field type/)

def main() -> None:
    pass

main()
