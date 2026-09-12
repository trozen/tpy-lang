# Multi-base class with __init__ on every base: the child calls
# BaseN.__init__(self, ...) for each; the generated C++ hoists both into the
# member initializer list.
from tpy import int32


class Named:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def describe(self) -> str:
        return self.name


class Counted:
    count: int32

    def __init__(self, count: int32) -> None:
        self.count = count

    def inc(self) -> None:
        self.count = self.count + 1


class Widget(Named, Counted):
    tag: str

    def __init__(self, name: str, count: int32, tag: str) -> None:
        Named.__init__(self, name)
        Counted.__init__(self, count)
        self.tag = tag


def main() -> None:
    w = Widget("button", int32(5), "ui")
    print(w.name)
    print(w.count)
    print(w.tag)
    w.inc()
    w.inc()
    print(w.count)


main()
