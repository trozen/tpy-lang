# bytearray.extend(Iterable[int32]) validates each element at runtime.
from tpy import int32


def main() -> None:
    ba = bytearray()
    xs: list[int32] = [100, 500]
    ba.extend(xs)


main()
