from tpy import Int32
class A:
    a: Int32
    def __init__(self, a: Int32) -> None:
        self.a = a
class B:
    b: Int32
    def __init__(self, b: Int32) -> None:
        self.b = b
def f(flag: bool) -> Int32:
    if flag:
        v: A | B = A(1)
        v = B(2)
        if isinstance(v, B):
            return v.b
    return 0
def main() -> None:
    print(f(True))
main()
