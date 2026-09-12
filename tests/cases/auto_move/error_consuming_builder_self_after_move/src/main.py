# The inverse of consuming_builder_chain: a consuming method's `self` is an
# owned source, so it is caught the way any moved local is when it is read
# again on the same path. `keep(self)` is not the last use (`return self` is),
# so the earlier owning slot cannot move it -- and for a `@nocopy` receiver
# there is no copy to fall back on.
from typing import Self
from tpy import int32, Own, nocopy


@nocopy
class Ticket:
    id: int32

    def __init__(self, id: int32) -> None:
        self.id = id

    def stamped(self: Own[Self]) -> Own[Self]:
        # `self` is read again below, so this owning slot cannot take it.
        keep(self)  # tpyc: error(/is used after this point and cannot be moved/)
        return self


def keep(t: Own[Ticket]) -> int32:
    return t.id


def main() -> None:
    print(Ticket(1).stamped().id)


main()
