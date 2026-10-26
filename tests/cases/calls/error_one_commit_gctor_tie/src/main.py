# A generic class's `@dispatch __init__` group ranks as a function's: a crossed
# tie is ambiguous, never the first declared (BUGS.md#dispatch-ctor-group-unlowered).
from tpy import int32, dispatch


class Animal:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()


class G[T]:
    r: str

    @dispatch
    def __init__(self, x: Dog, y: Animal, z: T) -> None:
        self.r = "DA"

    @dispatch
    def __init__(self, x: Animal, y: Dog, z: T) -> None:
        self.r = "AD"


def main() -> None:
    print(G(Dog(), Dog(), 1).r)  # tpyc: error(/Ambiguous overload for 'G.__init__'/)


main()
