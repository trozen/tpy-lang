# bytearray.extend(Iterable[Int32]) validates each element at runtime.
from tpy import Int32


def main() -> None:
    ba = bytearray()
    xs: list[Int32] = [100, 500]
    ba.extend(xs)


main()
