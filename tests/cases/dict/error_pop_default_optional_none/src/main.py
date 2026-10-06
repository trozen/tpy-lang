# `pop(k, None)` over an Optional value holding a class instance is refused at
# the default argument (BUGS.md#pop-default-optional-borrow-default-ill-formed).
from tpy import int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    d: dict[str, P | None] = {"a": P(1)}
    o = d.pop("a", None)  # tpyc: error(/method\.arg_shape/)
    if o is not None:
        print(o.n)


main()
