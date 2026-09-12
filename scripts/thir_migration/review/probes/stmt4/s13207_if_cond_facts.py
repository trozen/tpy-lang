from tpy import int32, ValueType
class A(ValueType):
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class B(ValueType):
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m
def take(u: A | B, flag: bool) -> int32:
    if not isinstance(u, A) or flag:
        return int32(0)
    else:
        return int32(1)
def main() -> None:
    print(take(A(3), False))
main()
