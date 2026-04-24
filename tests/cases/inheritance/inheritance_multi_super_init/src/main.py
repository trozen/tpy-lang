# Multi-base constructor: one base with __init__, one aggregate mixin.
# D22 v1 restricts multi-base classes to at most one __init__ base so
# super().__init__() in the child is unambiguous. The aggregate base
# (Counted) is default-constructed automatically.
from tpy import Int32


class Named:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def describe(self) -> str:
        return self.name


class Counted:
    count: Int32


class Widget(Named, Counted):
    def __init__(self, name: str, count: Int32) -> None:
        super().__init__(name)
        self.count = count


def main() -> None:
    w = Widget("button", Int32(5))
    print(w.describe())
    print(w.name)
    print(w.count)


main()
