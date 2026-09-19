# The COMPREHENSION-iterable position of a borrow-returning @property read off a
# TEMPORARY receiver -- the same fact as the for-each, one construct over. The
# capture would hold begin/end into storage the setup statement kills while what
# was lent (a GLOBAL here) outlives it. It compiled and SEGFAULTED before the
# position asked; the sibling tags are listed in
# records/error_property_off_temporary_receiver_arg.
# The dict-comp / set-comp / genexp legs below take the SAME tag, verified by
# compiling each on its own: one `SinkPos.ITER_SOURCE` row answers for every
# comprehension route as it does for the for-each, so there is nothing per-route
# to assert. They are unannotated because the compile stops at the first error.
# WORKAROUND: bind the receiver first -- `h = mk()` then `[x for x in h.items]`.
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


def main() -> None:
    doubled = [x + 1 for x in mk().items]  # tpyc: error(/foreach.iter_lends_from_temporary/)
    print(len(doubled))
    keyed = {x: x for x in mk().items}
    print(len(keyed))
    uniq = {x for x in mk().items}
    print(len(uniq))
    print(sum(x for x in mk().items))


main()
