from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

def first(xs: list[A]) -> A:
    return xs[0]
def pair(xs: list[A], n: int32) -> tuple[A, int32]:
    return (first(xs), n)
def main() -> None:
    xs = [A(1)]
    t = pair(xs, 2)
    print(t[1])
main()
