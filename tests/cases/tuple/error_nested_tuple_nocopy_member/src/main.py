# A @nocopy reference member nested inside an inner value tuple is rejected
# with the same message its depth-0 sibling gets, naming the nested element
# path -- the store copies, and @nocopy makes that a hard error rather than a
# warning.
from tpy import int32, nocopy


@nocopy
class H:
    fd: int32

    def __init__(self, fd: int32) -> None:
        self.fd = fd


def nested_nocopy(h: H) -> None:
    xs: list[tuple[int32, tuple[int32, H]]] = [(1, (2, h))]  # tpyc: error(/cannot copy non-copyable type 'H' into owned storage \(tuple element 1.1\)/)
    print(xs[0][1][1].fd)


def main() -> None:
    pass


main()
