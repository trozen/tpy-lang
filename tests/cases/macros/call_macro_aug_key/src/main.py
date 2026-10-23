# A call macro's expansion as an augmented-assignment key, and a call macro
# around calls its expansion analyzes again.
from datetime import timedelta
from io import StringIO
from typing import TypedDict

from aug_macros import clone_edit, first, identity, trimmed, twice
from tpy import Own, dispatch, int32, int64


def f(x: str) -> str:
    return x


def neg(v: int32) -> int32:
    return -v


class Opts(TypedDict):
    x: int64


def td_x(o: Opts) -> int64:
    return o["x"]


class Disp:
    def __init__(self) -> None:
        pass

    @dispatch
    def m(self, x: int64, k: int64) -> Own[list[int64]]:
        return [x * 10 + k]

    @dispatch
    def m(self, x: str, k: int64) -> Own[list[int64]]:
        return [k]


def main() -> None:
    s = "a-key-longer-than-the-small-string-buffer"
    d = {s: 0}
    # The key's hold proof reads the expansion `(" " + s).strip()`, which
    # runs nothing observable, so the key is re-read in place -- never a
    # held view over the concatenation's dying temporary.
    d[trimmed(s)] += 1  # tpyc: ok
    print("macro_key:", d[s])
    # An argument embedded twice is the analyzed node at both places, its
    # element read converted once for the `str` parameter. Evaluating it
    # at both places (`f` runs twice) is the filed defect
    # BUGS.md#call-macro-arg-evaluated-per-field, not the intended result.
    print("twice:", twice(f(s[0])))  # tpyc: ok
    # `ast.clone` copies: editing the clone leaves the argument as written.
    r: tuple[int32, int32] = clone_edit(7)  # tpyc: ok
    print("clone:", r)
    # The embedded call is analyzed again as the expansion's: its `reverse=`
    # and `key=` still bind by name.
    print("first-reverse:", first(sorted([3, 1], reverse=True)))  # tpyc: ok
    print("first-key:", first(sorted([3, 1, 2], key=neg)))  # tpyc: ok
    dm = Disp()
    # An overloaded method ranked on its keyword `k`, analyzed again.
    print("first-dispatch-method:", first(dm.m(2, k=3)))  # tpyc: ok
    # A TypedDict construction stays keyword-only when analyzed again.
    print("twice-td:", twice(td_x(Opts(x=2))))  # tpyc: ok
    o = identity(Opts(x=3))  # tpyc: ok
    print("identity-td:", o["x"])
    # A constructor overload group (OSError's) and a native-template
    # constructor resolved again.
    print("twice-ctor-group:", twice(OSError(2, "nope").errno))  # tpyc: ok
    print("twice-ctor-template:", twice(int64(2.5)))  # tpyc: ok
    # Record constructions with defaults left out, analyzed again.
    io = identity(StringIO())  # tpyc: ok
    io.write("io")
    print("identity-stringio:", io.getvalue())
    print("identity-timedelta:", identity(timedelta(1)).days)  # tpyc: ok


main()
