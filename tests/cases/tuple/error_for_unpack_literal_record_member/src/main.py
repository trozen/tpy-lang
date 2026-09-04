# The adjacent shape to the literal-iterable unpack: a RECORD member in the
# element tuple binds a borrow target, and the literal element is const, so the
# unpack head does not lower it.
class Row:
    n: int

    def __init__(self, n: int):
        self.n = n


def main() -> None:
    for name, r in [("a", Row(1)), ("b", Row(2))]:  # tpyc: error(/tuple.ref_target/)
        print(name, r.n)


main()
