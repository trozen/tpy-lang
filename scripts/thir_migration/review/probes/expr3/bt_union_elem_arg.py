from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

class B:
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m

def f(t: tuple[A | B, int32]) -> int32:
    return t[1]
def main() -> None:
    a = A(1)
    u: A | B = a
    print(f((u, 2)))
main()
