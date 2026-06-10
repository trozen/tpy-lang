# A Send-bounded type parameter satisfies a Send bound when forwarded to a
# nested generic (function and record) by reflexive forward-conformance.
from tpy import Own, Send


def sink[T: Send](x: Own[T]) -> None:
    print("sink")


def forward[T: Send](x: Own[T]) -> None:
    sink(x)


class Box[T: Send]:
    item: T

    def __init__(self, item: Own[T]) -> None:
        self.item = item


class Wrap[T: Send]:
    inner: Box[T]

    def __init__(self, item: Own[T]) -> None:
        self.inner = Box[T](item)


def main() -> None:
    forward(5)
    w = Wrap(7)
    print(w.inner.item)


main()
