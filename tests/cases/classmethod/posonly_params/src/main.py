# Positional-only parameters on a @classmethod: the `/` boundary is an index
# into the parameters after `cls`, so `a` is positional-only while `b` is not.
from tpy import int32


class Calc:
    @classmethod
    def add(cls, a: int32, /, b: int32) -> int32:
        return a + b


def main() -> None:
    print(Calc.add(1, 2))
    print(Calc.add(1, b=3))


main()
