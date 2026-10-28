# The call-ARGUMENT position of a borrow-returning @property read off a
# TEMPORARY receiver. The slot would bind a reference into an object that dies
# at the end of this statement, while what the getter lent can OUTLIVE that
# receiver -- here it lends a GLOBAL, which CPython mutates through -- so the
# copy a by-value slot would take loses every later mutation silently.
# `mutate` keeps nothing of its argument past the call (its body is
# measured), so the read is the bound twin's borrow -- `h = mk();
# mutate(h.items)` -- and stops where the twin does, at the argument-shape
# gate (BUGS.md#getter-result-slot-shape-rejects). A callee that keeps its
# argument refuses it at the rule's own tag, `arg.lends_from_temporary`
# (records/error_property_off_temporary_receiver_retaining_builtin).
# The full table -- one sink, one tag, one case each -- is in
# docs/PROPERTY_DESIGN.md, "A read off a TEMPORARY receiver: the sink table".
from tpy import Own, int32

G: list[int32] = [1, 2]


class H:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    @property
    def items(self) -> list[int32]:
        return G


def mk() -> Own[H]:
    return H()


def mutate(xs: list[int32]) -> None:
    xs.append(9)


def main() -> None:
    mutate(mk().items)  # tpyc: error(/call\.arg_shape\.container/)
    print(len(G))


main()
