# Mutating *args: Box body keeps the slot mutable -- the inference must NOT
# flip slots whose body actually writes through element references. Regression
# guard against an over-eager sync pass that flips on the presence of *args
# without consulting mutated_params.
from tpy import Int32


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def bump_all(*items: Box) -> None:  # tpyc: ok
    for b in items:
        b.val += 1


def main() -> None:
    a = Box(3)
    b = Box(4)
    bump_all(a, b)
    print(a.val)
    print(b.val)


main()
