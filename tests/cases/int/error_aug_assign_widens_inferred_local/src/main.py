# A local with no literal among its assignments keeps its first type, so an
# aug-assign whose result is wider is refused with the annotation to write.
from tpy import int32, int64


def grow(a32: int32, a64: int64) -> None:
    x = a32
    x += a64  # tpyc: error(/'x' is int32 \(line 7\) and 'x \+= a64' produces int64; annotate its first binding: x: int64/)
    print(x)


grow(1, 2)
