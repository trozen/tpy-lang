from tpy import Int32, ValueType
class A(ValueType):
    a: Int32
    def __init__(self, a: Int32) -> None:
        self.a = a
class B(ValueType):
    b: Int32
    def __init__(self, b: Int32) -> None:
        self.b = b
type AB = A | B
def main() -> None:
    xs: list[AB] = [A(1)]
    xs[0] = B(2)
    print(len(xs))
main()
