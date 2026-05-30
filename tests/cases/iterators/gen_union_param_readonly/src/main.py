# Regression: a readonly non-value union param to a resumable generator must
# use the deep-const pointer-variant (std::variant<const A*, const B*>) on
# both the factory signature and the call site -- a shallow-const variant
# would not bind the deep-const value the caller builds.
from typing import Iterator
from tpy import readonly


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Cat:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


def names(a: readonly[Dog | Cat]) -> Iterator[str]:
    yield "a"
    match a:
        case Dog():
            yield a.name
        case Cat():
            yield a.name
    yield "b"


def main() -> None:
    for s in names(Dog("rex")):
        print(s)


main()
