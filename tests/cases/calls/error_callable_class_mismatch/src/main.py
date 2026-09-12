# Error: callable class signature doesn't match Fn hint
from tpy import int32, Fn

class TakesTwo:
    def __call__(self, a: int32, b: int32) -> int32:
        return a + b

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def main():
    t = TakesTwo()
    apply(t, 5)  # tpyc: error(/no '__call__' overload .*matches/)

main()
