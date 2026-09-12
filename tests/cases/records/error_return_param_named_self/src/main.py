# A FREE function whose parameter is merely NAMED `self`: it is not a receiver,
# so the borrow-return's self arm cannot claim it and the bare-name source
# stays unadmitted -- returning it is rejected today.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(self: Box) -> Box:
    # `self` here is an ordinary parameter of a free function.
    return self  # tpyc: error(/stmt\.return:return\.record_source\.self\.borrow/)


def main() -> None:
    b = Box(1)
    print(f(b).n)


main()
