# Rule (List Literal Inference, dict and set literals): a typed first store into an empty dict decides its value.
# `f = {}` is seeded by `a8()`, so it holds int8 values; a wider store is refused.
from tpy import int8, int64


def a8() -> int8:
    return 100


def a64() -> int64:
    return 1099511627776


def main() -> None:
    f = {}
    f["a"] = a8()
    f["b"] = a64()  # tpyc: error(/'f' holds int8 values \(line 16\), and this value is int64; annotate its first binding: f: dict\[str, int64\] = \{\}/)


main()
