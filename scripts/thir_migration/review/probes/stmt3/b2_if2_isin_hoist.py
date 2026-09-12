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
def f(x: DC) -> int32:
    if isinstance(x, Dog):
        y = x.n
    else:
        y = x.m
    return y
def main() -> None:
    print(f(Dog(1)))
main()
