from tpy import int32
class A:
    n: int32
    def __init__(self) -> None:
        self.n = 1
class B:
    m: int32
    def __init__(self) -> None:
        self.m = 2
def size(u: A | B) -> int32:
    if isinstance(u, A):
        return u.n
    return 0
def go(u: A | B) -> int32:
    if isinstance(u, A):
        def f() -> int32:
            return size(u)
        return f()
    return 0
def main() -> None:
    print(go(A()))
main()
