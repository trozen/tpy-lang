# Pending container locals passed to CONSTRUCTOR params (plain, generic, raise)
# resolve against the param type; copy warnings name concrete types.
# (No overloaded-__init__ coverage: blocked by two pre-existing bugs in BUGS.md.)
from tpy import int32, int64, Own


class Holder:
    def __init__(self, xs: Own[list[int32]]):
        self.xs = xs


class Wrap[T]:
    def __init__(self, xs: Own[list[T]]):
        self.xs = xs


class DictHolder:
    def __init__(self, d: Own[dict[str, int32]]):
        self.d = d


class SetHolder:
    def __init__(self, s: Own[set[int32]]):
        self.s = s


class DataError(Exception):
    n: int32

    def __init__(self, xs: list[int32]):
        super().__init__()
        self.n = len(xs)


def main() -> None:
    other = [9]
    h = Holder(other)
    h.xs.append(5)
    print(h.xs)

    src = [1]
    w = Wrap(src)
    w.xs.append(2)
    print(w.xs)

    wide = [3]
    w2 = Wrap[int64](wide)
    print(w2.xs)

    dd = {"a": 1}
    dh = DictHolder(dd)  # tpyc: warning(/copies dict\[str, int32\] into owned storage/)
    print(dd["a"], dh.d["a"])

    zs = [3, 4]
    try:
        raise DataError(zs)
    except DataError as e:
        print(e.n)

    ss = {1, 2}
    sh = SetHolder(ss)  # tpyc: warning(/copies set\[int32\] into owned storage/)
    print(len(ss), len(sh.s))

main()
