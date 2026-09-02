from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

class B:
    m: Int32
    def __init__(self, m: Int32) -> None:
        self.m = m

def f(t: tuple[A, Int32]) -> Int32:
    return t[0].n
def g(x: A | B) -> Int32:
    if isinstance(x, A):
        return f((x, 1))
    return 0
def main() -> None:
    print(g(A(1)))
main()
