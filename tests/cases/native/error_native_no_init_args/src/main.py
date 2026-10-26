# A @native class with no __init__ and no fields has no constructor to take an
# argument; the program cannot give a binding one, so no @dataclass advice.
from tpy.extern import native


@native("nb::Handle")
class Handle:
    pass


def main() -> None:
    h = Handle(1)  # tpyc: error(/^.Handle. has no __init__$/)


main()
