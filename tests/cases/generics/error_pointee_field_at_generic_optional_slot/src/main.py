# A field DECLARED as the pointee passed at a substituted `U | None` slot: that
# needs an address-of lift, which only a storage-optional field's own conversion
# supplies, so the generic argument rejects.
class Pod:
    x: int

    def __init__(self) -> None:
        self.x = 0


class Holder:
    o: Pod

    def __init__(self) -> None:
        self.o = Pod()


def is_def_gen[U](o: U | None) -> bool:
    return o is not None


def main() -> None:
    t = Holder()
    print(is_def_gen(t.o))  # tpyc: error(/call\.generic_arg_shape/)


main()
