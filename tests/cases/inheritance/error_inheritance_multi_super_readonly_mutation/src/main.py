# super() in a @readonly multi-base method cannot dispatch to a non-readonly
# parent method (mirrors the single-base + unbound-self readonly guards).
from tpy import Int32, readonly


class Counter:
    n: Int32

    def __init__(self) -> None:
        self.n = Int32(0)

    def bump(self) -> None:
        self.n = self.n + 1


class Greeter:
    pass


class Wrapper(Counter, Greeter):
    @readonly
    def snapshot(self) -> Int32:
        super().bump()  # tpyc: error(/Cannot call non-readonly method 'bump' on readonly reference/)
        return self.n


print(0)
