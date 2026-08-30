# A bytes literal bound to a bytes field. Codegen has no comparison to emit for
# this literal kind, and used to drop the arm's condition silently -- emitting
# a dangling `} else {`. The kind matches the field, so the diagnostic names an
# unimplemented comparison rather than a type error.


class P:
    x: bytes

    def __init__(self, x: bytes) -> None:
        self.x = x


def describe(p: P) -> str:
    match p:
        case P(x=b"z"):  # tpyc: error(/bytes literal pattern against field 'x' of type 'bytes' is not yet implemented/)
            return "z"
        case _:
            return "other"


def main() -> None:
    pass


main()
