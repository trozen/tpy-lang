# Rule (List Literal Inference, dict and set literals): a typed first binding decides the value.
# `{"a": a8()}` is a dict[str, int8]; a wider store is refused with the annotation to write.
from tpy import int8, int64


def a8() -> int8:
    return 100


def wide() -> int64:
    return 1099511627776


def main() -> None:
    d = {"a": a8()}
    d["b"] = wide()  # tpyc: error(/'d' holds int8 values \(line 15\), and this value is int64; annotate its first binding: d: dict\[str, int64\] = \{\.\.\.\}/)


main()
