from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

def use[T](xs: list[T]) -> Int32:
    return len(xs)
def main() -> None:
    a = A(1)
    b = A(2)
    print(use([a, b]))
main()
