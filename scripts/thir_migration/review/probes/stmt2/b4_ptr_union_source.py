from tpy import int32
class A:
    a: int32
    def __init__(self, a: int32) -> None:
        self.a = a
class B:
    b: int32
    def __init__(self, b: int32) -> None:
        self.b = b
def f(items: list[A], flag: bool) -> int32:
    v: A | B = B(1)
    v = items[0]
    if isinstance(v, A):
        return v.a
    return 0
def main() -> None:
    print(f([A(1)], True))
main()
