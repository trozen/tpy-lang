# Assigning an inherited field with an incompatible type is a clean sema error
# (the inherited slot is typed), not a re-declaration and not a C++ error.
from tpy import Int32

class Base:
    x: Int32
    def __init__(self, x: Int32):
        self.x = x

class Child(Base):
    def __init__(self, x: str):
        self.x = x  # tpyc: error(/Type mismatch in assignment: expected Int32, got str/)

def main() -> None:
    Child("hi")

main()
