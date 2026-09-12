from tpy import int32
class Outer:
    class Inner:
        v: int32
        def __init__(self, v: int32, w: int32 = 0) -> None:
            self.v = v + w
def main() -> None:
    i = Outer.Inner(v=1)
    j = Outer.Inner(1, w=2)
    print(i.v, j.v)
main()
