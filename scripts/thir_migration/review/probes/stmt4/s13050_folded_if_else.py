from tpy import int32, ValueType
class A(ValueType):
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class B(ValueType):
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m
def f(u: A | B) -> int32:
    if isinstance(u, A):
        return u.n
    if isinstance(u, B):
        return u.m
    else:
        return 0
def main() -> None:
    print(f(A(3)))
main()
