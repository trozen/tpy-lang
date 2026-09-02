from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

class H:
    a: A
    def __init__(self, c: bool, other: A) -> None:
        self.a = A(1) if c else other
def main() -> None:
    o = A(2)
    h = H(True, o)
    print(h.a.n)
main()
