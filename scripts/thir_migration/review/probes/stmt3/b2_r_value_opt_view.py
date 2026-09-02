from tpy import Int32, Own, Ptr, StrView
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
def a(xs: list[str]) -> str | None:
    return xs[0]
def b(s: str) -> str | None:
    return s
def main() -> None:
    print(1 if a(['x']) is None else 0)
    print(1 if b('y') is None else 0)
main()
