# `Optional[Own[T]]` on a LOCAL is rejected as redundant -- a plain `T | None`
# local owns and moves out at last use (Own stays meaningful on params/returns).
from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def make_box(v: int) -> Own[Box]:
    return Box(v)


def main() -> None:
    t: Own[Box] | None = make_box(5)  # tpyc: error(/Own\[T\] is redundant in this variable type/)
    if t is not None:
        print(t.val)


main()
