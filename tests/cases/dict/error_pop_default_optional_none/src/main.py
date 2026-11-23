# `pop(k, z)` with a live named Optional default over a class is refused at the
# default argument (BUGS.md#pop-default-optional-borrow-default-ill-formed).
from tpy import int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    d: dict[str, P | None] = {"a": P(1)}
    z: P | None = P(2)
    o = d.pop("q", z)  # tpyc: error(/call\.own_opt_copy/)
    if o is not None and z is not None:
        print(o.n, z.n)


main()
