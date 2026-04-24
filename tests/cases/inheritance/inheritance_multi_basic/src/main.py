# Multi-inheritance (D22 v1): two disjoint mixins composed into one class.
# v1 restriction: at most one base may have __init__; here both are aggregate
# mixins (fields only, no __init__), so the child provides all construction.
from tpy import Int32


class Named:
    name: str

    def describe(self) -> str:
        return self.name


class Counted:
    count: Int32

    def inc(self) -> None:
        self.count = self.count + 1

    def value(self) -> Int32:
        return self.count


class Widget(Named, Counted):
    def __init__(self, name: str, count: Int32) -> None:
        self.name = name
        self.count = count


def main() -> None:
    w = Widget("button", Int32(5))
    print(w.name)
    print(w.count)
    print(w.describe())
    w.inc()
    w.inc()
    print(w.value())


main()
