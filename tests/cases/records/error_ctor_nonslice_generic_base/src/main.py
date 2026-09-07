# A generic BASE instantiated with a module-local union alias is outside the
# spelling-equal slice, so the derived constructor rejects.
from tpy import Int32, StrView


type Num = Int32 | StrView


class Box[T]:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class NumBox(Box[Num]):
    n: Int32

    def __init__(self, v: Int32, n: Int32) -> None:  # tpyc: error(/ctor.non_f1_base/)
        super().__init__(v)
        self.n = n


def main() -> None:
    print(NumBox(1, 2).n)


main()
