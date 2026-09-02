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
def f(x: DC, y: DC) -> Int32:
    if isinstance(x, Dog) and isinstance(y, Cat):
        return x.n + y.m
    else:
        return 0
def main() -> None:
    print(f(Dog(1), Cat(2)))
main()
