from tpy import Int32, ValueType
class A(ValueType):
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class B(ValueType):
    m: Int32
    def __init__(self, m: Int32) -> None:
        self.m = m
def f(u: A | B, flag: bool) -> Int32:
    t = Int32(0)
    while not (isinstance(u, A) or flag):
        t += 1
        flag = True
    return t
def main() -> None:
    print(f(B(3), False))
main()
