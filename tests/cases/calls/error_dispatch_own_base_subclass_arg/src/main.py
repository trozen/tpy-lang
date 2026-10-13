# A subclass argument picks the Own[Base] variant and is rejected (it would slice),
# never re-ranked to bare T (docs/OVERLOAD_DESIGN.md "An owned base slot").
from tpy import dispatch, Own, int32


class Animal:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()


@dispatch
def take[T](x: T) -> str:
    return "bare"


@dispatch
def take(x: Own[Animal]) -> str:  # tpyc: warning(/never consumed/)
    x.hits += 1
    return "own"


def main() -> None:
    print(take(Dog()))  # tpyc: error(/Type mismatch in argument 'x': expected Animal, got Dog/)


main()
