from tpy import int32
class A:
    a: int32
    def __init__(self, a: int32) -> None:
        self.a = a
class B:
    b: int32
    def __init__(self, b: int32) -> None:
        self.b = b
def f(p: A, q: B) -> int32:
    v: A | B = p
    if isinstance(v, A):
        v = q
        return 5
    return 0
def main() -> None:
    print(f(A(1), B(2)))
main()
