# Assigning an inherited field with an incompatible type is a clean sema error
# (the inherited slot is typed), not a re-declaration and not a C++ error.
from tpy import int32

class Base:
    x: int32
    def __init__(self, x: int32):
        self.x = x

class Child(Base):
    def __init__(self, x: str):
        self.x = x  # tpyc: error(/Type mismatch in assignment: expected int32, got str/)

def main() -> None:
    Child("hi")

main()
