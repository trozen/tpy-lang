# The receiver kind in a method set's ranking (docs/OVERLOAD_DESIGN.md "The
# receiver kind"): a mutable receiver ranks the mutating variants first and the
# readonly ones only when none of those applies; on a readonly receiver the
# arguments pick and the receiver kind breaks a tie -- for a subscript and an
# explicit call alike. The sections CPython's dispatch stand-in agrees with live in
# records/subscript_answers_as_call.
from tpy import Own, dispatch, int8, int32, int64, readonly
from policy_lib import Inferred


class Mixed:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def __getitem__(self, k: int64) -> int32:
        self.hits += 1
        return 200

    @dispatch
    @readonly
    def __getitem__(self, k: int32) -> int32:
        return k + 100


class Narrow:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def __getitem__(self, k: bool) -> int32:
        self.hits += 1
        return 8

    @dispatch
    @readonly
    def __getitem__(self, k: int64) -> int32:
        return 64

    @dispatch
    @readonly(False)
    def m(self, k: bool) -> int32:
        self.hits += 1
        return 8

    @dispatch
    @readonly
    def m(self, k: int64) -> int32:
        return 64


class NarrowKey:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def __getitem__(self, k: int8) -> int32:
        self.hits += 1
        return 8

    @dispatch
    @readonly
    def __getitem__(self, k: int64) -> int32:
        return 64

    @dispatch
    @readonly(False)
    def m(self, k: int8) -> int32:
        self.hits += 1
        return 8

    @dispatch
    @readonly
    def m(self, k: int64) -> int32:
        return 64


class Twins:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly
    def __getitem__(self, k: int64) -> int32:
        return 1

    @dispatch
    @readonly(False)
    def __getitem__(self, k: int64) -> int32:
        self.hits += 1
        return 2


def make_narrow() -> Own[Narrow]:
    return Narrow()


def read_mixed(b: readonly[Mixed]) -> None:
    # readonly receiver: only the readonly variant applies
    print("mixed_readonly:", b[1], b.__getitem__(1), b.hits)  # tpyc: ok


def read_twins(w: readonly[Twins]) -> None:
    n: int64 = 0
    print("twins_readonly:", w[n], w.__getitem__(n), w.hits)  # tpyc: ok


def main() -> None:
    # mixed: a mutable receiver ranks the mutating variant first
    b = Mixed()
    print("mixed:", b[1], b.__getitem__(1), b.hits)  # tpyc: ok
    read_mixed(b)

    # narrow: the readonly fallback beside a mutating variant C++ would prefer
    # passes its receiver const, so the variant ranked is the one that runs
    nw = Narrow()
    n32: int32 = 5
    print("narrow:", nw[n32], nw.m(n32), nw.hits)  # tpyc: ok
    # receiver kind ranks before conversion cost: the mutating int8 variant
    # takes the narrowed key, in the subscript and the method form alike
    nk = NarrowKey()
    print("narrow key:", nk[n32], nk.m(n32), nk.hits)  # tpyc: ok
    # temporary: a temporary receiver is passed const the same way (a
    # subscript of a call is not lowered yet: subscript.record_getitem)
    print("temporary:", make_narrow().m(n32))  # tpyc: ok

    # twins: one key type, the receiver picks the face
    w = Twins()
    n: int64 = 0
    print("twins:", w[n], w.__getitem__(n), w.hits)  # tpyc: ok
    read_twins(w)

    # inferred: variants readonly only by inference rank by their arguments
    # alone, the same whether the class is imported (as here) or local
    inf = Inferred()
    print("inferred:", inf.m(1), inf.hits)  # tpyc: ok


main()
