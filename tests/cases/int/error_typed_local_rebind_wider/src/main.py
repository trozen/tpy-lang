# A local with no literal among its assignments has the type of its first
# assignment: a narrower value converts into it, a wider one is refused with
# the annotation to write.
from tpy import int32, int64


def main(a: int32, b: int64) -> None:
    x = a
    # the wider binding of the int32 local
    x = b  # tpyc: error(/'x' is int32 \(line 8\) and this value is int64; annotate its first binding: x: int64 = .../)
    print(x)


main(1, 2)
