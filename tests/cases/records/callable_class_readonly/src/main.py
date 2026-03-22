# Callable class with @readonly __call__
from tpy import Int32, readonly

class Negate:
    @readonly
    def __call__(self, x: Int32) -> Int32:
        return -x

class ScaleBy:
    factor: Int32
    def __init__(self, factor: Int32):
        self.factor = factor
    @readonly
    def __call__(self, x: Int32) -> Int32:
        return x * self.factor

def main():
    n = Negate()
    print(n(42))
    print(n(-7))

    s = ScaleBy(3)
    print(s(10))
    print(s(-5))

main()
