# A typed parameter gives an unannotated list literal a wider element than
# its literal's default. A read that its consumer resolves is compiled at the
# consumer's type and stays correct; the values are above 2**31 so a read
# compiled at the default width would print a truncated number. A read that
# commits to the default width is refused:
# tests/cases/list/error_widened_literal_list_read.
from tpy import int32, int64


def big(v: list[int64]) -> None:
    v.append(5000000000)


def same(v: list[int32]) -> None:
    v.append(7)


def show(n: int64) -> int64:
    return n + 1


def resolved_by_consumer() -> None:
    ys = [1]
    big(ys)
    # function: an annotated local, a print argument, a comparison and an
    # int64 parameter each give the read its type.
    m: int64 = ys[1]  # tpyc: ok
    print("fn.annotated", m)
    print("fn.print", ys[1])  # tpyc: ok
    print("fn.compare", ys[1] > 4000000000)  # tpyc: ok
    print("fn.argument", show(ys[1]))  # tpyc: ok


def read_before() -> None:
    ys = [1]
    # function: the same reads placed before the widening call.
    m: int64 = ys[0]  # tpyc: ok
    print("fn.before", m, ys[0])  # tpyc: ok
    big(ys)
    print("fn.before_len", len(ys))


def append_first() -> None:
    w: int64 = 5000000000
    ys = [1]
    ys.append(w)
    # function: a wider append before the read widens the element the read
    # sees, so an unannotated local takes it.
    n = ys[1]  # tpyc: ok
    print("fn.append_first", n)


def default_width() -> None:
    ys = [1]
    n = ys[0]  # tpyc: ok
    same(ys)
    # function: a parameter of the literal's own default width decides
    # nothing new, so unannotated reads on either side of the call stand.
    k = ys[1]  # tpyc: ok
    print("fn.default_width", n, k)


class Holder:
    n: int64

    def __init__(self) -> None:
        self.n = 0

    def read(self) -> None:
        ys = [1]
        big(ys)
        # method: an int64 field gives the read its type.
        self.n = ys[1]  # tpyc: ok
        print("method.field", self.n)


def main() -> None:
    resolved_by_consumer()
    read_before()
    append_first()
    default_width()
    Holder().read()


main()
