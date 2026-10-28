# An operator's operand is an argument of the dunder it resolves to: a
# dunder that returns its operand keeps it past the operation, so a field of
# a temporary's getter there would leave `y` pointing into storage the
# statement destroys (`b + mk().ch.rec` with `__add__` returning `o`).
# The verdict is the dunder's own (`operand_retention`); one that keeps
# nothing admits the same operand, pinned by records/stub_arg_off_temporary.
# The rule: an operand a user dunder returns or keeps may not be a read off
# a temporary (docs/LANGUAGE_FEATURES.md, the `@property` entry).
# WORKAROUND: bind the receiver first -- `h = mk()` then `b + h.ch.rec`.
from tpy import Own, int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Child:
    rec: Rec

    def __init__(self) -> None:
        self.rec = Rec(5)


class H:
    child: Child

    def __init__(self) -> None:
        self.child = Child()

    @property
    def ch(self) -> Child:
        return self.child


def mk() -> Own[H]:
    return H()


class Bag:
    def __add__(self, o: Rec) -> Rec:
        return o


def main() -> None:
    b = Bag()
    y = b + mk().ch.rec  # tpyc: error(/lends_from_temporary/)
    print(y.n)


main()
