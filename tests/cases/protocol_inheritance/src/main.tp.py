from typing import Protocol, Sized
from tpy import Int32


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

    def __len__(self) -> Int32:
        return Int32(5)


class Container[T: PrintableAndSized]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def describe(self) -> None:
        print(self.value.to_str())
        print(len(self.value))


def main() -> None:
    msg = Message("hello")
    c = Container(msg)
    c.describe()


main()
