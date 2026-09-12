from tpy import int32, Own, char
class Dog:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class Cat:
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m
type DC = Dog | Cat
def main() -> None:
    xs: list[DC] = [Dog(1), Cat(2)]
    xs[0] = Dog(3)
    print(len(xs))
main()
