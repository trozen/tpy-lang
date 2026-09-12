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
    t = int32(0)
    while not isinstance(u, B) and flag:
        t += int32(1)
        flag = False
    return t
def main() -> None:
    print(take(A(3), True))
main()
