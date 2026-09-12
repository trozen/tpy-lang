from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

def main() -> None:
    n = A(1)
    c = True
    xs: list[tuple[A | None, int32]] = [(n if c else None, 1)]
    print(len(xs))
main()
