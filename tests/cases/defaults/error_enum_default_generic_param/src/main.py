# An enum-member default on a type-param-containing parameter is rejected: an
# enum member is monomorphic (only meaningful for a concrete enum-typed param),
# unlike a literal default which is polymorphic and deferred to instantiation.
# TPy-only -- CPython accepts any object default regardless of annotation.
from enum import Enum


class Color(Enum):
    RED = 0
    GREEN = 1


def pick[T](x: T = Color.RED) -> T:  # tpyc: error(/requires a concrete enum-typed parameter/)
    return x


def main() -> None:
    print(int(pick(Color.GREEN).value))


main()
