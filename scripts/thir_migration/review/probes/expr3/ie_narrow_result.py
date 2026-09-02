from tpy import Int32
class A:
    a: Int32
    def __init__(self, a: Int32) -> None:
        self.a = a
class B:
    b: Int32
    def __init__(self, b: Int32) -> None:
        self.b = b
def pick(u: A | B) -> str:
    return 'a' if isinstance(u, A) else 'b'
def main() -> None:
    print(pick(A(1)))
main()
