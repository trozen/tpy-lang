from typing import Protocol, Sized
from tpy import int32, Comparable, Send, ValueType


class Printable(Protocol):
    def to_str(self) -> str: ...


class PrintableAndSized(Printable, Sized, Protocol):
    pass


class Message:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text

    def to_str(self) -> str:
        return self.text

    def __len__(self) -> int32:
        return int32(5)


class Container[T: PrintableAndSized]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def describe(self) -> None:
        print(self.value.to_str())
        print(len(self.value))


class OrdVal(Comparable, ValueType, Protocol): ...


class SendSized(Send, Sized, Protocol): ...


# a member-less protocol is the intersection of its parents: each parent,
# a marker included, decides by its own rule
def top[T: OrdVal](xs: list[T]) -> T:  # tpyc: ok
    best = xs[0]
    for x in xs:
        if best < x:
            best = x
    return best


def size_of[T: SendSized](x: T) -> int:  # tpyc: ok
    return len(x)


def main() -> None:
    msg = Message("hello")
    c = Container(msg)
    c.describe()
    print("marker_parents:", top([3, 9, 2]), top(["a", "c"]), size_of([1, 2, 3]))


main()
