# MIR verdicts for calls to user `@native` stubs: MIR admits only a declared contract
# over readonly leaf parameters, so each body here is refused at its stub call.
# tpy: include("native_types.hpp")
from tpy import int32, pure, Comparable, StrView, String
from tpy.extern import native


class Rec:
    n: int32

    def __init__(self, n: int32):
        self.n = n


@pure
@native("probe_view")
def probe_view(s: str) -> StrView: ...


@native("probe_plain")
def probe_plain(x: float) -> float: ...


@native("probe_tick")
def probe_tick() -> float: ...


@pure
@native("probe_text")
def probe_text(x: Comparable) -> str: ...


@native("probe_fill", transient=True)
def probe_fill(s: String) -> None: ...


@native("probe_bump", transient=True)
def probe_bump(r: Rec) -> None: ...


# free function: a stub with neither @pure nor transient=True
def unmarked(x: float) -> float:  # tpyc: mir(uncovered /^stub declares no contract$/)
    return probe_plain(x)


# free function: lending no argument is no contract, an unmarked stub may reach any storage
def unmarked_nullary() -> float:  # tpyc: mir(uncovered /^stub declares no contract$/)
    return probe_tick()


# free function: the str result may borrow what the protocol parameter binds, and an int32 has no storage
def text_of_scalar(i: int32) -> int32:  # tpyc: mir(uncovered /^stub result may borrow a scalar argument$/)
    return len(probe_text(i))


# free function: a transient stub taking a record by mutable reference
def mut_ref(r: Rec) -> None:  # tpyc: mir(uncovered /^stub parameter is not a readonly leaf$/)
    probe_bump(r)


# free function: a transient stub declares no const verdict for its String
def mutable_leaf(s: String) -> None:  # tpyc: mir(uncovered /^stub parameter is not a readonly leaf$/)
    probe_fill(s)


# free function: a @pure stub returning a view borrows its lent argument
def view_result(s: str) -> int32:  # tpyc: mir(covered)
    v = probe_view(s)  # tpyc: mir_borrowed(v) mir_borrows(v, s)
    return 1


def main() -> None:
    print("unmarked:", unmarked(1.5))
    print("unmarked_nullary:", unmarked_nullary())
    print("text_of_scalar:", text_of_scalar(42))
    r = Rec(1)
    mut_ref(r)
    print("mut_ref:", r.n)
    s = String("ab")
    mutable_leaf(s)
    print("mutable_leaf:", s)
    print("view_result:", view_result("xyz"))


main()
