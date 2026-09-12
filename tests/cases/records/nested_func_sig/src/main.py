# Nested types in module-level function signatures (params and returns)
from tpy import int32, Own
from enum import Enum, auto

class Container:
    class Inner:
        val: int32
        def __init__(self, val: int32) -> None:
            self.val = val

    class Kind(Enum):
        A = auto()
        B = auto()

def make_inner(v: int32) -> Own[Container.Inner]:
    return Container.Inner(v)

def take_inner(i: Container.Inner) -> int32:
    return i.val

def get_kind() -> Container.Kind:
    return Container.Kind.A

def main() -> None:
    i = make_inner(42)
    print(take_inner(i))
    print(get_kind())

main()
