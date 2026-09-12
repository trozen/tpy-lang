# bytes(Iterable[int32]) validates each element is in 0..255 at runtime.
from tpy import int32


def main() -> None:
    xs: list[int32] = [200, 999]
    print(bytes(xs))


main()
