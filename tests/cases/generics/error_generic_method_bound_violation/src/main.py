# Error: per-method bound violation -- method requires T: Comparable but concrete type doesn't satisfy it
from tpy import Comparable

class Wrapper:
    def __init__(self):
        pass

class Container[T]:
    items: list[T]

    def __init__(self):
        self.items = []

    def is_sorted[T: Comparable](self) -> bool:
        return True

def main() -> None:
    c: Container[Wrapper] = Container[Wrapper]()
    c.is_sorted()  # tpyc: error(/requires type parameter 'T' to satisfy 'Comparable'/)

main()
