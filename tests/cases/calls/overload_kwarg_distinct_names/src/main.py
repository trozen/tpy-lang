# Overloads with distinct keyword-only param names + defaulted positional gap.
#
# Two regression dimensions the same-name-different-type test doesn't cover:
#
#   1. The kwarg-name-unknown-on-this-overload path. Each overload has its
#      own kwonly name; a call with one kwarg should reject every overload
#      that doesn't declare that name. Before the fix, resolve_overload saw
#      no kwargs and both overloads tied on positional [Int32] alone,
#      raising "Ambiguous overload" before the post-resolution
#      `_resolve_call_kwargs` could even map the kwarg.
#
#   2. The defaulted-gap fill path inside `_expand_arg_types_with_kwargs`.
#      The middle param `b` has a default; with only the first positional
#      and a trailing kwarg supplied, the expansion fills the gap with
#      `p.type` so cross-overload scoring isn't skewed.
from typing import overload
from tpy import Int32


@overload
def report(a: Int32, b: Int32 = 0, *, mode: str = "x") -> str:
    return mode + ":" + str(a + b)


@overload
def report(a: Int32, b: Int32 = 0, *, count: Int32 = 1) -> Int32:
    return (a + b) * count


def main() -> None:
    # Distinct-kwarg-name dispatch: only one overload accepts each kwarg.
    s: str = report(Int32(3), mode="tag")              # "tag:3"
    print(s)
    n: Int32 = report(Int32(3), count=Int32(4))        # 12
    print(n)
    # Defaulted-gap path: middle `b` defaulted, kwarg supplied after.
    # Expansion fills the b slot with the param type so neither overload
    # gets a scoring edge from the gap.
    s2: str = report(Int32(2), Int32(5), mode="sum")    # "sum:7"
    print(s2)
    n2: Int32 = report(Int32(2), Int32(5), count=Int32(3))  # 21
    print(n2)


main()
