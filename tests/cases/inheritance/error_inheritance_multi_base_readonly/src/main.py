# A @readonly method cannot dispatch through the unbound-self form to an
# ancestor method that would mutate self. The readonly guard mirrors the one
# on regular self.method() calls and on super() dispatch.
from tpy import Int32, readonly


class Counter:
    n: Int32

    def __init__(self) -> None:
        self.n = Int32(0)

    def bump(self) -> None:
        self.n = self.n + 1


class Wrapper(Counter):
    @readonly
    def snapshot(self) -> Int32:
        Counter.bump(self)  # tpyc: error(/Cannot call non-readonly method 'bump' on readonly reference/)
        return self.n


print(0)
