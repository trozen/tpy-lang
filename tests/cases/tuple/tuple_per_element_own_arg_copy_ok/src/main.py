# A copy()'d element in a name-bound tuple passed into a per-element-Own tuple
# param is accepted (the hazard set is empty) -- the positive arg-NAME path.
from tpy import Int32, Own, copy


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def sink(p: tuple[Own[Box], Int32]) -> Int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    return p[0].val + p[1]


def f(b: Box) -> Int32:
    pair = (copy(b), 0)
    return sink(pair)


def main() -> None:
    print(f(Box(5)))  # 5


main()
