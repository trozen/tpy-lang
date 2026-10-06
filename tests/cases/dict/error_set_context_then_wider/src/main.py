# Rule (List Literal Inference, dict and set literals): a typed set the literal meets decides its element.
# Passed as set[int32], `{1}` is a set[int32]; a wider element is refused naming the call.
from tpy import int32, int64


def wide() -> int64:
    return 1099511627776


def takeset32(s: set[int32]) -> None:
    s.add(9)


def main() -> None:
    s = {1}
    takeset32(s)
    s.add(wide())  # tpyc: error(/'s' holds int32 elements since line 16 \(passed as set\[int32\]\), and this value is int64; the set is passed as set\[int32\], so the value must be int32/)


main()
