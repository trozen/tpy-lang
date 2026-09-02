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
def f(x: DC) -> Int32:
    if isinstance(x, Dog):
        y = x.n
    else:
        y = x.m
    return y
def main() -> None:
    print(f(Dog(1)))
main()
