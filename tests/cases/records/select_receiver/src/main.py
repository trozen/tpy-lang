# A ternary or walrus expression as a method-call receiver: the receiver aliases
# the chosen operand, so a mutating method must be visible through its name.
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def bump(self) -> Int32:
        self.n += 1
        return self.n


def ternary_record(c: bool) -> None:
    a = Counter(1)
    b = Counter(10)
    print((a if c else b).bump())  # the chosen operand is mutated in place
    print(a.n, b.n)


def walrus_record() -> None:
    a = Counter(5)
    print((q := a).bump())  # the walrus binds an alias, not a copy
    print(q.bump(), a.n)


def ternary_str(c: bool) -> None:
    s = "ab"
    t = "cd"
    print((s if c else t).upper())


def walrus_str() -> None:
    print((w := "xy").upper(), w)


def main() -> None:
    ternary_record(True)
    ternary_record(False)
    walrus_record()
    ternary_str(True)
    ternary_str(False)
    walrus_str()


main()
