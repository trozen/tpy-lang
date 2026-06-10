# A scalar `Optional[Own[T]]` LOCAL is the one exemption to the "Own is
# redundant in a local annotation" rule: a plain `Optional[T]` local defaults to
# a BORROW (`T*`), so `Optional[Own[T]]` is how you spell an OWNED nullable local
# (`std::optional<T>`). It must compile (the exemption), and the owned shape
# keeps the value alive past the owning call that produced it -- a borrow would
# dangle on the returned temporary.
from typing import Optional

from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def make_box(v: int) -> Own[Box]:
    return Box(v)


def main() -> None:
    t: Optional[Own[Box]] = make_box(5)  # tpyc: ok
    if t is not None:
        t.val += 1
        print(t.val)


main()
