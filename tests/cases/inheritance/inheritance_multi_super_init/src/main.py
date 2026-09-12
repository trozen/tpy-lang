# Only Named has __init__; super().__init__() resolves to it. The aggregate
# base (Counted) is default-constructed automatically.
from tpy import int32


class Named:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def describe(self) -> str:
        return self.name


class Counted:
    count: int32


class Widget(Named, Counted):
    def __init__(self, name: str, count: int32) -> None:
        super().__init__(name)
        self.count = count


def main() -> None:
    w = Widget("button", int32(5))
    print(w.describe())
    print(w.name)
    print(w.count)


main()
