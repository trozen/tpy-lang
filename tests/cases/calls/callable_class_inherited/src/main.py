# Inherited __call__: child class inherits callable behavior from parent
from tpy import Int32, Fn

class Base:
    def __call__(self, x: Int32) -> Int32:
        return x * 2

class Child(Base):
    pass

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def main():
    c = Child()
    print(c(5))
    print(apply(c, 10))

main()
