# An unpack target the tuple hands its owned element over to is a fresh
# local: a reference into it may not be returned, as for `a = P()`
# (docs/LANGUAGE_FEATURES.md "Owned Return Values").
from tpy import Own, int32


class P:
    data: list[int32]

    def __init__(self) -> None:
        self.data = [42]


def take(t: tuple[Own[P], int32]) -> list[int32]:
    a, k = t  # t's last use: a owns element 0
    return a.data  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(take((P(), 0))[0])


main()
