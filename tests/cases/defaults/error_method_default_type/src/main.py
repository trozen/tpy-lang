# Method parameter defaults go through the same type check as free-function
# ones -- methods and __init__ register through a separate call site.
from tpy import int32


class Counter:
    n: int32

    def __init__(self, start: int32 = 0) -> None:
        self.n = start

    def bump(self, by: int32 = None) -> int32:  # tpyc: error(/expected int32, got None/)
        self.n = self.n + by
        return self.n


def main() -> None:
    c = Counter()
    print(c.bump())


main()
