# A user's own body-less bindings (@native, @cpp_template) take an existing
# container at an `Iterable[int32]` parameter in place, as a TPy-bodied callee does.
__tpy__ = {"includes": ["native_types.hpp"]}
from typing import Iterable
from tpy import int32
from tpy.extern import cpp_template, native


@native("nt::total")
def total(xs: Iterable[int32]) -> int32: ...


@cpp_template("static_cast<int32_t>(std::ranges::distance({0}))")
def count(xs: Iterable[int32]) -> int32: ...


class H:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]


def main() -> None:
    ll = [[5, 4], [6]]
    h = H()
    # the element and the field bind the user binding's parameter by reference
    print("native", total(ll[0]), total(h.xs))  # tpyc: ok
    print("template", count(ll[1]), count(h.xs))  # tpyc: ok


main()
