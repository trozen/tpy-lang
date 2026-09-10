# Method overloads disambiguated by keyword-argument type.
#
# Mirrors `overload_kwarg_disambig` for the method-call code path in
# sema/methods.py: the multi-overload branch of `_resolve_and_check_args`
# now threads `kwarg_types` through `resolve_overload` and runs
# `resolve_kwargs` against the winning signature post-resolution. Before
# the fix, the method path raised "Keyword arguments not supported for
# overloaded method 'apply'" outright.
from tpy import Int32, dispatch


class Box:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    @dispatch
    def apply(self, x: Int32, *, tag: str = "") -> str:
        return tag + ":" + str(self.base + x)

    @dispatch
    def apply(self, x: Int32, *, tag: Int32 = 0) -> Int32:
        return self.base + x + tag


def main() -> None:
    b = Box(Int32(100))
    a: str = b.apply(Int32(5), tag="sum")
    print(a)                                # sum:105
    c: Int32 = b.apply(Int32(5), tag=Int32(7))
    print(c)                                # 112


main()
