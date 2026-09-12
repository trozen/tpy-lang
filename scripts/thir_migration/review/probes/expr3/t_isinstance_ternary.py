from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

class B:
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m

class C:
    k: int32
    def __init__(self, k: int32) -> None:
        self.k = k
def f(x: A | B | C) -> int32:
    return 1 if isinstance(x, A) else 2
def main() -> None:
    print(f(A(1)))
main()
