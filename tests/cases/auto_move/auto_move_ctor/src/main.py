# Auto-move for record constructor with Own[T] parameter.
from tpy import int32, Own


class Inner:
    value: int32


class Outer:
    inner: Inner

    def __init__(self, inner: Own[Inner]):
        self.inner = inner  # tpyc: ok

    def get_value(self) -> int32:
        return self.inner.value


def main():
    inner = Inner()
    inner.value = 99
    # inner is at last use -- auto-moved into Outer constructor
    outer = Outer(inner)
    print(outer.get_value())


main()
