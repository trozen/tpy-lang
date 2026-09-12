from tpy import int32, basic_slice
class R:
    lo: int32 | None
    def __init__(self) -> None:
        self.lo = 1
def main() -> None:
    r = R()
    s = basic_slice(r.lo, None)
    xs = [1, 2, 3]
    ys = xs[s]
    print(ys[0])
main()
