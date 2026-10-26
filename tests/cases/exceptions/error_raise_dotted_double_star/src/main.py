# `raise Outer.Err(**d)` is refused as the expression `Outer.Err(**d)` is;
# CPython runs it (BUGS.md#unpack-into-named-params-rejects).
from tpy import int32


class Outer:
    class Err(Exception):
        def __init__(self, code: int32) -> None:
            super().__init__("err")
            self.code = code


def main() -> None:
    d = {"code": 1}
    raise Outer.Err(**d)  # tpyc: error(/does not accept \*\*kwargs/)


main()
