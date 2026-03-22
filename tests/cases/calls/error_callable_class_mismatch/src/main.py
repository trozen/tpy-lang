# Error: callable class signature doesn't match Fn hint
from tpy import Int32, Fn

class TakesTwo:
    def __call__(self, a: Int32, b: Int32) -> Int32:
        return a + b

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:  # tpyc: ok
    return f(x)

def main():
    t = TakesTwo()
    apply(t, 5)  # tpyc: error(/__call__.*does not match/)

main()
