# Inherited __call__: child class inherits callable behavior from parent
from tpy import int32, Fn

class Base:
    def __call__(self, x: int32) -> int32:
        return x * 2

class Child(Base):
    pass

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def main():
    c = Child()
    print(c(5))
    print(apply(c, 10))

main()
