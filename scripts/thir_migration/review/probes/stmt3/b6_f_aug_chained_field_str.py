from tpy import int32
class Inner:
    t: str
    def __init__(self) -> None:
        self.t = 'a'
class Outer:
    i: Inner
    def __init__(self) -> None:
        self.i = Inner()
def main() -> None:
    o = Outer()
    o.i.t += 'b'
    print(o.i.t)
main()
