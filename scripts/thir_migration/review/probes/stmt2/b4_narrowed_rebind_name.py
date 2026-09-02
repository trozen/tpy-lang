from tpy import Int32
class A:
    a: Int32
    def __init__(self, a: Int32) -> None:
        self.a = a
class B:
    b: Int32
    def __init__(self, b: Int32) -> None:
        self.b = b
def f(p: A, q: B) -> Int32:
    v: A | B = p
    if isinstance(v, A):
        v = q
        return 5
    return 0
def main() -> None:
    print(f(A(1), B(2)))
main()
