from tpy import Int32, ValueType
class A(ValueType):
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class B(ValueType):
    m: Int32
    def __init__(self, m: Int32) -> None:
        self.m = m
def take(u: A | B, flag: bool) -> Int32:
    if not isinstance(u, A) or flag:
        return Int32(0)
    else:
        return Int32(1)
def main() -> None:
    print(take(A(3), False))
main()
