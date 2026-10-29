# A readonly receiver runs the readonly twin of a set whose variants differ
# only in receiver kind, at every call form (docs/OVERLOAD_DESIGN.md "The
# receiver kind"): a method call, super(), an explicit Base.m(self, ...) and
# an `in` test on a readonly haystack that is not a plain name. The readonly
# twin is declared first, so CPython's dispatch stand-in runs it too.
from tpy import dispatch, readonly, int8, int32, int64


class C:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    @readonly
    def m(self, x: int32) -> int32:
        return 2

    @dispatch
    @readonly(False)
    def m(self, x: int32) -> int32:
        self.n += 1
        return 1

    @dispatch
    @readonly
    def w(self, x: int64) -> int32:
        return 4

    @dispatch
    @readonly(False)
    def w(self, x: int64) -> int32:
        self.n += 1
        return 3

    @dispatch
    @readonly
    def __contains__(self, x: int32) -> bool:
        return x == 2

    @dispatch
    @readonly(False)
    def __contains__(self, x: int32) -> bool:
        self.n += 1
        return False


class D(C):
    def __init__(self) -> None:
        super().__init__()

    @readonly
    def via_self(self) -> int32:
        return self.m(1)  # tpyc: ok

    @readonly
    def via_super(self) -> int32:
        # super() ranks the set for the readonly self
        return super().m(1)  # tpyc: ok

    @readonly
    def via_base(self) -> int32:
        # Base.m(self, ...) ranks it as super() does
        return C.m(self, 1)  # tpyc: ok


def via_convert(c: readonly[C], k: int8) -> int32:
    # both twins take the int8 only by a conversion: the tie-break holds in
    # the conversion stage too
    return c.w(k)  # tpyc: ok


class Holder:
    c: C

    def __init__(self) -> None:
        self.c = C()


def in_field(h: readonly[Holder]) -> bool:
    # a readonly haystack reached through a field ranks as a readonly receiver
    return 2 in h.c  # tpyc: ok


def main() -> None:
    d = D()
    print("method:", d.via_self(), d.n)
    print("super:", d.via_super(), d.n)
    print("base:", d.via_base(), d.n)
    c = C()
    print("convert:", via_convert(c, 1), c.n)
    h = Holder()
    print("in:", in_field(h), h.c.n)


main()
