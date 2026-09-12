# Pass callable objects to Fn parameters (zero-cost template dispatch)
from tpy import int32, Fn

class Doubler:
    def __call__(self, x: int32) -> int32:
        return x * 2

class Adder:
    offset: int32
    def __init__(self, offset: int32):
        self.offset = offset
    def __call__(self, x: int32) -> int32:
        return x + self.offset

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def apply_twice(f: Fn[[int32], int32], x: int32) -> int32:
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
