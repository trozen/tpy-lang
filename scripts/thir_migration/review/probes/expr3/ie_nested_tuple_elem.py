from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

def main() -> None:
    n = A(1)
    c = True
    xs: list[tuple[A | None, Int32]] = [(n if c else None, 1)]
    print(len(xs))
main()
