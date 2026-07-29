# Field defaults share the parameter defaults' type check -- unchecked, a
# `None` on a non-Optional field emitted `int32_t n = std::nullopt`.
from tpy import Int32


class Slot:
    n: Int32 = None  # tpyc: error(/expected Int32, got None/)

    def __init__(self) -> None:
        pass


def main() -> None:
    s = Slot()
    print(s.n)


main()
