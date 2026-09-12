from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

class B:
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m

def f(t: tuple[A, int32]) -> int32:
    return t[0].n
def g(x: A | B) -> int32:
    if isinstance(x, A):
        return f((x, 1))
    return 0
def main() -> None:
    print(g(A(1)))
main()
