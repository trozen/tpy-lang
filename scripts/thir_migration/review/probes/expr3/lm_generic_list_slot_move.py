from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

def use[T](xs: list[T]) -> int32:
    return len(xs)
def main() -> None:
    a = A(1)
    b = A(2)
    print(use([a, b]))
main()
