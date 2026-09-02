from tpy import Int32
class A:
    a: Int32
    def __init__(self, a: Int32) -> None:
        self.a = a
class B:
    b: Int32
    def __init__(self, b: Int32) -> None:
        self.b = b
def f(items: list[A], flag: bool) -> Int32:
    v: A | B = B(1)
    v = items[0]
    if isinstance(v, A):
        return v.a
    return 0
def main() -> None:
    print(f([A(1)], True))
main()
