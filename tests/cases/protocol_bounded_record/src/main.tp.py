from typing import Protocol
from tpy import Int32, Own, Comparable

# A generic record with a bounded type parameter
class SortedPair[T: Comparable]:
    first: T
    second: T

    def __init__(self, a: T, b: T) -> None:
        if a < b:
            self.first = a
            self.second = b
        else:
            self.first = b
            self.second = a

# Protocol that references the bounded generic record
class PairFactory(Protocol):
    def make_pair(self, a: Int32, b: Int32) -> Own[SortedPair[Int32]]: ...

class DefaultPairFactory:
    def make_pair(self, a: Int32, b: Int32) -> Own[SortedPair[Int32]]:
        return SortedPair[Int32](a, b)

def create_pair[T: PairFactory](factory: T, a: Int32, b: Int32) -> Own[SortedPair[Int32]]:
    return factory.make_pair(a, b)

def main() -> None:
    factory = DefaultPairFactory()
    pair = create_pair(factory, 30, 10)
    print(pair.first)
    print(pair.second)

main()
