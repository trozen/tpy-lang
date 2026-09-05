# The adjacent shape a container read must keep rejecting at a CONCRETE
# container slot (`dict.update` / `set.update` take a `dict` / `set`, not a
# structural Iterable), now that the plain field read of a local receiver is
# admitted there: the same read reached through a NESTED receiver, which the
# admitted row's own receiver check does not cover. The ELEMENT-read flavour
# is its own case (list/error_container_field_elem_concrete_slot): a rejected
# module reports only its first blocking construct.
from tpy import Int32


class Inner:
    ages: dict[str, Int32]

    def __init__(self) -> None:
        self.ages = {"a": 1}


class Holder:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner()


def main() -> None:
    h = Holder()
    d: dict[str, Int32] = {}
    d.update(h.inner.ages)  # tpyc: error(/method.arg_shape/)
    print(len(d))


main()
