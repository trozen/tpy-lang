from tpy import Int32, ValueType
class A(ValueType):
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class B(ValueType):
    m: Int32
    def __init__(self, m: Int32) -> None:
        self.m = m
def f(u: A | B) -> Int32:
    if isinstance(u, A):
        del u
        return 1
    return 0
def main() -> None:
    print(f(A(3)))
main()
