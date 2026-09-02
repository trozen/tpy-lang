from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

def first(xs: list[A]) -> A:
    return xs[0]
def pair(xs: list[A], n: Int32) -> tuple[A, Int32]:
    return (first(xs), n)
def main() -> None:
    xs = [A(1)]
    t = pair(xs, 2)
    print(t[1])
main()
