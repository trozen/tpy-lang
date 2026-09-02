from tpy import Int32, Span, readonly
def total(sp: Span[readonly[Int32]]) -> Int32:
    t = 0
    for x in sp:
        t += x
    return t
class Buf:
    xs: list[Int32]
    def __init__(self) -> None:
        self.xs = [1, 2]
    def __span__(self) -> Span[readonly[Int32]]:
        return self.xs
    def s(self) -> Int32:
        return total(self)
def f(b: Buf | None) -> Int32:
    if b is not None:
        return total(b)
    return 0
def main() -> None:
    b = Buf()
    print(b.s(), f(b))
main()
