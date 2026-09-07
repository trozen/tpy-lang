# A match subject reached through an OPTIONAL intermediate link: the deref
# check that link needs cannot be spelled inside the subject lift; the same
# line also draws the ordinary optional-access warning.
from tpy import Int32


class Box:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val


class Mid:
    opt: Box | None

    def __init__(self) -> None:
        self.opt = Box(7)


class Outer:
    mid: Mid | None

    def __init__(self) -> None:
        self.mid = Mid()


def f(o: Outer) -> None:
    # `o.mid` is itself optional, one link before the subject.
    match o.mid.opt:  # tpyc: error(/stmt\.match:field\.opt_receiver/)
        case None:
            print("none")
        case Box() as bb:
            print(bb.val)


def main() -> None:
    f(Outer())


main()
