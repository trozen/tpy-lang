from tpy import int32, Own, char
class Dog:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class Cat:
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m
class Fish:
    k: int32
    def __init__(self, k: int32) -> None:
        self.k = k
class Bird:
    w: int32
    def __init__(self, w: int32) -> None:
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
