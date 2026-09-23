# A mixed tuple local from a call (owned element beside a borrowed one) warns its
# borrowed element's copy at an owning insert, then rejects (plan D1): loud.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def ins(xs: list[tuple[Box, Box]], b: Box) -> None:
    t = make_mixed(b)
    # The insert row admits only a local whose tuple type is the slot's.
    xs.append(t)  # tpyc: warning(/tuple element 1\)/) error(/method.arg_shape/)


def main() -> None:
    xs: list[tuple[Box, Box]] = []
    ins(xs, Box(2))
    print(len(xs))


main()
