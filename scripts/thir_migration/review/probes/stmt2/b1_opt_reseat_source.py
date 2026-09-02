from tpy import Int32, readonly
class Inner:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
class Slot:
    opt: Inner | None
    def __init__(self) -> None:
        self.opt = None
def peek(slots: list[Slot], i: Int32) -> Int32:
    box = slots[i].opt
    box = slots[0].opt
    if box is None:
        return 0
    return box.v
def main() -> None:
    xs = [Slot()]
    print(peek(xs, 0))
main()
