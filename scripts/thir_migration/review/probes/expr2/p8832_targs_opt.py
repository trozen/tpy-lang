from tpy import Int32
class Bag:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
    def conv[T](self, x: T) -> T:
        return x
def f(b: Bag | None) -> Int32:
    return b.conv(Int32(3))
def main() -> None:
    print(f(Bag(1)))
main()
