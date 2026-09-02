from tpy import Int32
class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def main() -> None:
    p: P | None = None
    p = P(1)
    def reset() -> None:
        nonlocal p
        p = P(2)
    reset()
    if p is not None:
        print(p.x)
main()
