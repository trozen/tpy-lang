from tpy import Int32, Own
class Dog:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class Cat:
    m: Int32
    def __init__(self, m: Int32) -> None:
        self.m = m
type DC = Dog | Cat
def a(xs: list[DC]) -> DC:
    return xs[0]
def main() -> None:
    xs: list[DC] = [Dog(1)]
    print(1)
main()
