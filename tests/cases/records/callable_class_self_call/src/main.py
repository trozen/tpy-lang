# Callable class: self(args) recursion inside __call__
from tpy import Int32

class Recursive:
    def __call__(self, x: Int32) -> Int32:
        if x <= 0:
            return 0
        return self(x - 1) + 1

class Fibonacci:
    def __call__(self, n: Int32) -> Int32:
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
