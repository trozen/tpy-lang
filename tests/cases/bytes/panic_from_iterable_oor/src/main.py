# bytes(Iterable[Int32]) validates each element is in 0..255 at runtime.
from tpy import Int32


def main() -> None:
    xs: list[Int32] = [200, 999]
    print(bytes(xs))


main()
