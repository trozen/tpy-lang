from tpy import int32, Own
class Dog:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class Cat:
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m
type DC = Dog | Cat
def a(xs: list[DC]) -> DC:
    return xs[0]
def main() -> None:
    xs: list[DC] = [Dog(1)]
    print(1)
main()
