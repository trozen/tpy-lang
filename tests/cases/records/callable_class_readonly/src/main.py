# Callable class with @readonly __call__
from tpy import int32, readonly

class Negate:
    @readonly
    def __call__(self, x: int32) -> int32:
        return -x

class ScaleBy:
    factor: int32
    def __init__(self, factor: int32):
        self.factor = factor
    @readonly
    def __call__(self, x: int32) -> int32:
        return x * self.factor

def main():
    n = Negate()
    print(n(42))
    print(n(-7))

    s = ScaleBy(3)
    print(s(10))
    print(s(-5))

main()
