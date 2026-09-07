# A borrow-return whose source is an isinstance-NARROWED union member: the read
# renames to the extraction alias, which is not the bare name the return arm
# admits, so returning the narrowed name is rejected.
from tpy import Int32


class Box:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Inner:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


def f(u: Box | Inner, d: Inner) -> Inner:
    if isinstance(u, Inner):
        # `u` is the narrowed read, not a plain name.
        return u  # tpyc: error(/stmt\.return:return\.record_source\.narrowed\.borrow/)
    return d


def main() -> None:
    print(f(Inner(2), Inner(9)).value)


main()
