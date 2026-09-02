from tpy import Int32
class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
from tpy import readonly
def f(items: readonly[list[P | None]]) -> None:
    ys = [v.x if v is not None else -1 for v in items]
    print(ys)
def main() -> None:
    xs: list[P | None] = [P(1), None]
    f(xs)
main()
