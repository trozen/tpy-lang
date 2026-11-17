# docs/LANGUAGE_FEATURES.md "Copy Warnings for Inline Storage": only a static
# iterating protocol is an owning element slot, so a `@dynamic` protocol with
# an `Own` type argument is none: a non-conforming argument gets the
# conformance error.
from typing import Protocol
from tpy import Own, dynamic, int32


@dynamic
class Bag[T](Protocol):
    def __iter__(self) -> "Bag[T]": ...
    def get(self) -> T: ...


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


def take(b: Bag[Own[R]]) -> int32:
    return b.get().v


def main() -> None:
    # The non-conforming argument at the `@dynamic` slot is the subject.
    print(take(5))  # tpyc: error(/does not conform to protocol Bag/)


main()
