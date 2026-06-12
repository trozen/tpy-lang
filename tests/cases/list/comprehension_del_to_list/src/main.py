# A fixed-length comprehension of a record with __del__ resolves to list: the
# Array build can't default-construct a default-ctor-suppressed element.
from tpy import Int32


class Res:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v

    def __del__(self) -> None:
        pass


def main() -> None:
    xs = [Res(i) for i in range(4)]  # tpyc: type(/list\[Res\]/)
    total = 0
    for r in xs:
        total += r.v
    print(total)


main()
