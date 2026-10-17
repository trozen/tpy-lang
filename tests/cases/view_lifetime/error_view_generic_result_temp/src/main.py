# A view bound from a generic stub call whose result is its type parameter
# (dict.pop -> V at StrView) can be the temporary argument it was given.
from tpy import StrView, int32


def mk(n: int32) -> str:
    return f"long-string-{n}-padding-padding-padding-padding-padding"


def dpop(d: dict[int32, StrView]) -> None:
    # The default `mk(3)` is returned when 5 is missing, and dies here.
    v: StrView = d.pop(5, mk(3))  # tpyc: error(/temporary view source/)
    print(v)


def main() -> None:
    d: dict[int32, StrView] = {}
    dpop(d)


main()
