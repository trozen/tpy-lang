from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

class B:
    m: Int32
    def __init__(self, m: Int32) -> None:
        self.m = m

class C:
    k: Int32
    def __init__(self, k: Int32) -> None:
        self.k = k
def f(x: A | B | C) -> Int32:
    return 1 if isinstance(x, A) else 2
def main() -> None:
    print(f(A(1)))
main()
