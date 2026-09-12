from tpy import int32, ValueType
class A(ValueType):
    a: int32
    def __init__(self, a: int32) -> None:
        self.a = a
class B(ValueType):
    b: int32
    def __init__(self, b: int32) -> None:
        self.b = b
type AB = A | B
def main() -> None:
    xs: list[AB] = [A(1)]
    xs[0] = B(2)
    print(len(xs))
main()
