# A list subscript answers as its `__getitem__` call: a str key matches no
# variant, with the explicit call's message.
from tpy import int32


def main() -> None:
    xs: list[int32] = [1, 2]
    print(xs["a"])  # tpyc: error(/Type mismatch in list index: expected int32 or slice, got str/)


main()
