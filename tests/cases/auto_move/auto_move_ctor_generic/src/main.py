# Auto-move Own[T] in generic class __init__ uses std::move.
from tpy import Int32, Own


class Inner:
    value: Int32


class Box[T]:
    item: T

    def __init__(self, item: Own[T]):
        self.item = item


def main():
    inner = Inner()
    inner.value = 42
    box = Box[Inner](inner)
    print(box.item.value)


main()
