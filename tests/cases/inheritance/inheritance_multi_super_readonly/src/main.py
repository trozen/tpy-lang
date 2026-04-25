# super() in a @readonly multi-base method must dispatch to @readonly parent
# methods on each branch.
from tpy import Int32, readonly


class Counter:
    n: Int32

    def __init__(self) -> None:
        self.n = Int32(0)

    @readonly
    def get_n(self) -> Int32:
        return self.n


class Greeter:
    @readonly
    def greet(self) -> str:
        return "hi"


class Both(Counter, Greeter):
    def __init__(self) -> None:
        super().__init__()

    @readonly
    def describe(self) -> str:
        return super().greet() + ":" + str(super().get_n())


def main() -> None:
    b = Both()
    print(b.describe())


main()
