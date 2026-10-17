# Method results discarded at statement position, by result family: a value
# union and a recursive-union wrapper. The call still runs for its effect.
from tpy import Own, int32
from tree_types import Tree


class Factory:
    calls: int32

    def __init__(self) -> None:
        self.calls = 0

    def un(self, k: int32) -> int32 | str:
        self.calls += 1
        if k > 0:
            return k
        return "s"

    def make(self) -> Own[Tree]:
        self.calls += 1
        return [1, [2]]


def value_union() -> None:
    f = Factory()
    # The value-union result is dropped: a bare call statement.
    f.un(1)  # tpyc: ok
    print("value_union:", f.calls)


def recursive_wrapper() -> None:
    f = Factory()
    # The recursive-union wrapper result is dropped: a bare call statement.
    f.make()  # tpyc: ok
    print("recursive_wrapper:", f.calls)


def main() -> None:
    value_union()
    recursive_wrapper()


main()
