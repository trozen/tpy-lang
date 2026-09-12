from tpy import int32
class A:
    a: int32
    def __init__(self, a: int32) -> None:
        self.a = a
class B:
    b: int32
    def __init__(self, b: int32) -> None:
        self.b = b
def pick(u: A | B) -> str:
    return 'a' if isinstance(u, A) else 'b'
def main() -> None:
    print(pick(A(1)))
main()
