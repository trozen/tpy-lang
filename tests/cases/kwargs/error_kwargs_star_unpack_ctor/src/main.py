# Error: `**expr` into a constructor without `**kwargs` is rejected by the
# argument binding, as it is at a function (`error_kwargs_star_unpack`).
# CPython unpacks the dict into the named parameters: a restriction TPy
# does not document yet (BUGS.md#unpack-into-named-params-rejects).
from typing import TypedDict


class TD(TypedDict):
    a: int


class R:
    def __init__(self, a: int = 5) -> None:
        self.a = a


def main() -> None:
    d = TD(a=7)
    r = R(**d)  # tpyc: error(/'R\(\)' does not accept \*\*kwargs/)
    print(r.a)


main()
