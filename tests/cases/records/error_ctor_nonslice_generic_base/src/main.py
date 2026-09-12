# A generic BASE instantiated with a module-local union alias is outside the
# spelling-equal slice, so the derived constructor rejects.
from tpy import int32, StrView


type Num = int32 | StrView


class Box[T]:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class NumBox(Box[Num]):
    n: int32

    def __init__(self, v: int32, n: int32) -> None:  # tpyc: error(/ctor.non_f1_base/)
        super().__init__(v)
        self.n = n


def main() -> None:
    print(NumBox(1, 2).n)


main()
