from tpy import int32, readonly
class Inner:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
class Slot:
    opt: Inner | None
    def __init__(self) -> None:
        self.opt = None
def peek(slots: list[Slot], i: int32) -> int32:
    box = slots[i].opt
    box = slots[0].opt
    if box is None:
        return 0
    return box.v
def main() -> None:
    xs = [Slot()]
    print(peek(xs, 0))
main()
