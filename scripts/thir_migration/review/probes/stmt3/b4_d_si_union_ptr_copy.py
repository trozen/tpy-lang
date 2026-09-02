from tpy import Int32, Own, Char
class Dog:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class Cat:
    m: Int32
    def __init__(self, m: Int32) -> None:
        self.m = m
class Fish:
    k: Int32
    def __init__(self, k: Int32) -> None:
        self.k = k
class Bird:
    w: Int32
    def __init__(self, w: Int32) -> None:
        self.w = w
type DC = Dog | Cat
type DCF = Dog | Cat | Fish
type DCFB = Dog | Cat | Fish | Bird
def main() -> None:
    xs: list[DC] = [Dog(1), Cat(2)]
    d = Dog(3)
    xs[0] = d
    print(d.n)
    print(len(xs))
main()
