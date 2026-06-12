# Match on a storage-form Optional source (`Box | None` field) lifts the
# subject to pointer form; mutation through the arm binding aliases the field.
from tpy import Int32


class Box:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val


class Holder:
    opt: Box | None

    def __init__(self) -> None:
        self.opt = Box(7)


def main() -> None:
    h = Holder()
    match h.opt:
        case None:
            print("none")
        case Box() as bb:
            bb.val = 99
    if h.opt is not None:
        print(h.opt.val)
    h.opt = None
    match h.opt:
        case None:
            print("none2")
        case Box():
            print("box2")


main()
