from tpy import Int32
class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
class H:
    v: Int32
    def __init__(self) -> None:
        self.v = 1
        p: P | None = None
        p = P(1)
        def reset() -> None:
            nonlocal p
            p = P(2)
        reset()
        if p is not None:
            self.v = p.x
def main() -> None:
    print(H().v)
main()
