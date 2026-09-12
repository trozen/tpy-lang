# A generic instance whose nested type argument is a module-LOCAL plain union
# alias: that alias registers after lowering, so the outer generic rejects.
from tpy import int32, Own, StrView
from tplib.box import Box

type Num = int32 | StrView
type Tree[T] = T | list[Tree[T]]


class Holder:
    data: Box[Tree[Num]]

    def __init__(self, data: Own[Box[Tree[Num]]]) -> None:
        self.data = data


def main() -> None:
    seed: Tree[Num] = [int32(1)]
    h = Holder(Box(seed))  # tpyc: error(/call\.ctor_arg/)
    print("ok")


main()
