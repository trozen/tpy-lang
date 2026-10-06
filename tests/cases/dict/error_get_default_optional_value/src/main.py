# `get(k, default)` over an Optional value holding a class instance is refused
# (BUGS.md#get-default-composite-value-refused).
from tpy import int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    d: dict[str, P | None] = {"a": P(1)}
    fb: P | None = None
    o = d.get("a", fb)  # tpyc: error(/'P \| None' holds a reference type, which 'get\(...\)' would copy/)
    if o is not None:
        o.n += 1


main()
