# A float local with no literal among its assignments has the type of its
# first one, as an int local does: a wider float value is refused with the
# annotation to write.
from tpy import float32


def main(narrow: float32, wide: float) -> None:
    x = narrow
    # the float value into the float32 local
    x = wide  # tpyc: error(/'x' is float32 \(line 8\) and this value is float; annotate its first binding: x: float = \.\.\./)
    print(x)


main(float32(1.5), 0.1)
