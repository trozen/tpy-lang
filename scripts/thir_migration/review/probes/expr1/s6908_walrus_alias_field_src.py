from tpy import Int32
class Rec:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class H:
    inner: Rec
    def __init__(self) -> None:
        self.inner = Rec(1)
def f(h: H) -> Int32:
    if (q := h.inner).n > 0:
        return q.n
    return 0
def main() -> None:
    pass
main()
