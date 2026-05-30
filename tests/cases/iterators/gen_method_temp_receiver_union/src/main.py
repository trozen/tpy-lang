# Regression: a generator method on a temporary receiver whose field is a union
# (the "receiver variant" report) -- the receiver must be lifted to stay live.
from typing import Iterator


class Dog:
    def __init__(self) -> None:
        pass


class Cat:
    def __init__(self) -> None:
        pass


class Box:
    payload: Dog | Cat

    def __init__(self, p: Dog | Cat) -> None:
        self.payload = p

    def describe(self) -> Iterator[str]:
        yield "start"
        match self.payload:
            case Dog():
                yield "dog"
            case Cat():
                yield "cat"


def main() -> None:
    for s in Box(Dog()).describe():
        print(s)
    for s in Box(Cat()).describe():
        print(s)


main()
