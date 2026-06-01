# A named-constant field comparison is rejected on a union subject too (the
# rejection lives in the shared field resolver, not just the polymorphic path).
import enum


class Size(enum.Enum):
    SMALL = 1
    BIG = 2


class Dog:
    size: Size
    def __init__(self, size: Size) -> None:
        self.size = size

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def describe(a: Dog | Cat) -> str:
    match a:
        case Dog(size=Size.BIG):  # tpyc: error(/comparing field 'size' against a named constant is not supported/)
            return "big"
        case _:
            return "other"
