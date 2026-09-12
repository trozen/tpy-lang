# Callable class: __call__ method generates operator() in C++
from tpy import int32

class Doubler:
    def __call__(self, x: int32) -> int32:
        return x * 2

class Adder:
    offset: int32
    def __init__(self, offset: int32):
        self.offset = offset
    def __call__(self, x: int32) -> int32:
        return x + self.offset

def main():
    d = Doubler()
    print(d(5))
    print(d(100))

    a = Adder(10)
    print(a(5))
    print(a(32))

main()
