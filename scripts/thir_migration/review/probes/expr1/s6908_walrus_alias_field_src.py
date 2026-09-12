from tpy import int32
class Rec:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class H:
    inner: Rec
    def __init__(self) -> None:
        self.inner = Rec(1)
def f(h: H) -> int32:
    if (q := h.inner).n > 0:
        return q.n
    return 0
def main() -> None:
    pass
main()
