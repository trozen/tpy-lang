# The second adjacent shape a container read must keep rejecting at a
# CONCRETE container slot (`dict.update` takes a `dict`, not a structural
# Iterable), now that the plain field read of a local receiver is admitted
# there: an ELEMENT read off a container field. The sibling case pins the
# other flavour, a read through a NESTED receiver -- a rejected module
# reports only its first blocking construct, so the two need separate cases
# (list/error_container_field_chain_concrete_slot).
from tpy import Int32


class Holder:
    rows: list[dict[str, Int32]]

    def __init__(self) -> None:
        self.rows = [{"a": 1}]


def main() -> None:
    h = Holder()
    d: dict[str, Int32] = {}
    d.update(h.rows[0])  # tpyc: error(/method\.arg_shape/)
    print(len(d))


main()
