# The walrus spelling of rebinding a tuple-of-references global from a function
# body: its own sema arm, the same refusal.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


V = Box(1)
W = Box(2)
pair_g: tuple[Box, int32] = (V, 1)


def rebind() -> int32:
    global pair_g
    return (pair_g := (W, 2))[1]  # tpyc: error(/Cannot reassign global variable 'pair_g' of a tuple of reference types/)


def main() -> None:
    print(rebind())


main()
