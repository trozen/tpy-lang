# Overloads with distinct keyword-only param names + defaulted positional gap.
#
# Two regression dimensions the same-name-different-type test doesn't cover:
#
#   1. The kwarg-name-unknown-on-this-overload path. Each overload has its
#      own kwonly name; a call with one kwarg should reject every overload
#      that doesn't declare that name, rather than tie on the positional
#      [int32] alone and report an ambiguous overload.
#
#   2. The defaulted-gap fill path inside `_expand_arg_types_with_kwargs`.
#      The middle param `b` has a default; with only the first positional
#      and a trailing kwarg supplied, the expansion fills the gap with
#      `p.type` so cross-overload scoring isn't skewed.
from tpy import int32, dispatch


@dispatch
def report(a: int32, b: int32 = 0, *, mode: str = "x") -> str:
    return mode + ":" + str(a + b)


@dispatch
def report(a: int32, b: int32 = 0, *, count: int32 = 1) -> int32:
    return (a + b) * count


# Generic variants called by keyword pick the same winner whichever is
# declared first: the variant tried first and rejected must leave the call
# as written for the next one.
@dispatch
def pick[T](x: T, y: list[T]) -> str:
    return "list"


@dispatch
def pick[T](y: T, x: str) -> str:
    return "str"


def main() -> None:
    # Distinct-kwarg-name dispatch: only one overload accepts each kwarg.
    s: str = report(int32(3), mode="tag")              # "tag:3"
    print(s)
    n: int32 = report(int32(3), count=int32(4))        # 12
    print(n)
    # Defaulted-gap path: middle `b` defaulted, kwarg supplied after.
    # Expansion fills the b slot with the param type so neither overload
    # gets a scoring edge from the gap.
    s2: str = report(int32(2), int32(5), mode="sum")    # "sum:7"
    print(s2)
    n2: int32 = report(int32(2), int32(5), count=int32(3))  # 21
    print(n2)
    # generic keyword order: `x` is a str, so only the second variant binds.
    print("generic-order", pick(y=3, x="s"))  # tpyc: ok
    print("generic-order", pick(x="s", y=3))  # tpyc: ok


main()
