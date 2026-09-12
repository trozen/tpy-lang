from tpy import int32
class Bag:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def conv[T](self, x: T) -> T:
        return x
def f(b: Bag | None) -> int32:
    return b.conv(int32(3))
def main() -> None:
    print(f(Bag(1)))
main()
