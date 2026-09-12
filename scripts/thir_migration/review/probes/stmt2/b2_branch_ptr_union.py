from tpy import int32
class A:
    a: int32
    def __init__(self, a: int32) -> None:
        self.a = a
class B:
    b: int32
    def __init__(self, b: int32) -> None:
        self.b = b
def f(flag: bool) -> int32:
    if flag:
        v: A | B = A(1)
        v = B(2)
        if isinstance(v, B):
            return v.b
    return 0
def main() -> None:
    print(f(True))
main()
