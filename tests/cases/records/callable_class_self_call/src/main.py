# Callable class: self(args) recursion inside __call__
from tpy import int32

class Recursive:
    def __call__(self, x: int32) -> int32:
        if x <= 0:
            return 0
        return self(x - 1) + 1

class Fibonacci:
    def __call__(self, n: int32) -> int32:
        if n <= 1:
            return n
        return self(n - 1) + self(n - 2)

def main():
    r = Recursive()
    print(r(5))
    print(r(0))

    f = Fibonacci()
    print(f(10))

main()
