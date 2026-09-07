# A method call whose receiver walks a field CHAIN with a non-F1 middle link (a
# generic record over a module-local union alias): the link walk is the fence,
# so the receiver rejects.
from tpy import Int32, Own, StrView

type Num = Int32 | StrView


class Holder[T]:
    tag: T
    xs: list[Int32]

    def __init__(self, tag: T):
        self.tag = tag
        self.xs = []


class Outer:
    mid: Holder[Num]

    def __init__(self, mid: Own[Holder[Num]]):
        self.mid = mid


def use(o: Outer) -> None:
    o.mid.xs.append(1)  # tpyc: error(/method\.recv\.field_chain/)


def main() -> None:
    use(Outer(Holder[Num](Int32(1))))


main()
