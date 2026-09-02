from tpy import Int32, Own, Char
class Dog:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class Cat:
    m: Int32
    def __init__(self, m: Int32) -> None:
        self.m = m
type DC = Dog | Cat
def main() -> None:
    xs: list[DC] = [Dog(1), Cat(2)]
    xs[0] = Dog(3)
    print(len(xs))
main()
