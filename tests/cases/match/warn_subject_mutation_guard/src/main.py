# Subject-storage mutation in a GUARD also warns (bindings are emitted
# before the guard runs). Runtime: the guard's pop empties the tail only.
from tpy import Int32


def poke(xs: list[Int32]) -> None:
    match xs[0]:
        case x if xs.pop() > 100:  # tpyc: warning(/'xs\[0\]' is mutated in this arm while pattern bindings borrow/)
            print("big tail", x)
        case y:
            print("small", y)


def main() -> None:
    poke([5, 50])


main()
