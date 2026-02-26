# Error: invalid mixin type for enum (not an integer type)
from enum import Enum

class Foo:
    pass

class Bad(Foo, Enum):  # tpyc: error(/Invalid enum mixin type/)
    A = 0

def main() -> None:
    print(Bad.A)

main()
