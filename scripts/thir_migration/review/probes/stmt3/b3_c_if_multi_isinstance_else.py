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
def f(x: DC, y: DC) -> int32:
    if isinstance(x, Dog) and isinstance(y, Cat):
        return x.n + y.m
    else:
        return 0
def main() -> None:
    print(f(Dog(1), Cat(2)))
main()
