# A lambda returning a tuple LITERAL at a pointer-repr tuple slot: each element
# needs its own borrow lift, which the closure return has no arm for.
# Concretely, `lambda p: (str(p.x), p)` inside `map(...)`; TPy rejects that
# lambda body today.
from tpy import Int32, copy_iter


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def __str__(self) -> str:
        return f"P{self.x}"


def main() -> None:
    pts = [Point(1), Point(2)]
    d = dict(copy_iter(map(lambda p: (str(p.x), p), pts)))  # tpyc: error(/call\.native_arg/)
    for k in d:
        print(k, d[k])


main()
