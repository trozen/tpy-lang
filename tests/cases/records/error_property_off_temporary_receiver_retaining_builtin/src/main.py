# An argument slot may hold a borrow of storage the statement kills only when
# the CALLEE reads it INSIDE the call. `reversed` does not: it returns a lazy
# iterator that keeps a reference to what it was given, the loop then walks it
# after the `mk()` temporary is destroyed, and the elements printed are
# whatever is left in the freed storage. `enumerate`, `zip`, `map`, `filter`
# and `itertools.islice` retain the same way, and so does every generator or
# coro factory (its frame stores the argument); `len`, `sum`, `any`, `sorted`,
# `str()` and `list()` read eagerly and are admitted, pinned by
# records/property_read_temporary_transient.
# The verdict is the callee's own retention facts, not the argument's type:
# `return_borrows_from` and `addr_escapes_params` for a bodied callee, the
# declared signature for a body-less stub; the two gaps that leaves are filed
# as BUGS.md#native-stub-declares-no-param-retention.
# The sibling positions and their tags are listed in
# records/error_property_off_temporary_receiver_arg.
# WORKAROUND: bind the receiver first -- `h = mk()` then `reversed(h.data)`.
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
    for x in reversed(mk().items):  # tpyc: error(/arg.lends_from_temporary/)
        print(x)


main()
