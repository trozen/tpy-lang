from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

class Inner:
    a: A
    def __init__(self) -> None:
        self.a = A(1)
class H:
    inner: Inner
    def __init__(self) -> None:
        self.inner = Inner()
def pair(h: H, n: Int32) -> tuple[A, Int32]:
    return (h.inner.a, n)
def main() -> None:
    h = H()
    t = pair(h, 2)
    print(t[1])
main()
