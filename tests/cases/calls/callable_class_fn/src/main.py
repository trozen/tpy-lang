# Pass callable objects to Fn parameters (zero-cost template dispatch)
from tpy import Int32, Fn

class Doubler:
    def __call__(self, x: Int32) -> Int32:
        return x * 2

class Adder:
    offset: Int32
    def __init__(self, offset: Int32):
        self.offset = offset
    def __call__(self, x: Int32) -> Int32:
        return x + self.offset

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def apply_twice(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(f(x))

def main():
    d = Doubler()
    print(apply(d, 5))
    print(apply(d, 100))
    print(apply_twice(d, 3))

    a = Adder(10)
    print(apply(a, 5))
    print(apply_twice(a, 0))

main()
