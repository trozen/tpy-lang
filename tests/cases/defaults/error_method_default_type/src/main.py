# Method parameter defaults go through the same type check as free-function
# ones -- methods and __init__ register through a separate call site.
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self, start: Int32 = 0) -> None:
        self.n = start

    def bump(self, by: Int32 = None) -> Int32:  # tpyc: error(/expected Int32, got None/)
        self.n = self.n + by
        return self.n


def main() -> None:
    c = Counter()
    print(c.bump())


main()
