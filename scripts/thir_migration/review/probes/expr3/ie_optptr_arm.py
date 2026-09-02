from tpy import Int32
class N:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
def main() -> None:
    ns = [N(1), N(2)]
    c = True
    p: N | None = ns[0] if c else None
    if p is not None:
        p.v = 9
    print(ns[0].v)
main()
