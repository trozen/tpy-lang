# The shared field-write receiver ladder admits a nested receiver, but the
# CONST-ness of the chain root is still sema's: writing a `str` field through
# a `@readonly` self must stay an error, exactly as the scalar write does.
from tpy import int32, readonly


class Inner:
    name: str
    count: int32

    def __init__(self) -> None:
        self.name = "a"
        self.count = 0


class Outer:
    inner: Inner
    rows: list[Inner]

    def __init__(self) -> None:
        self.inner = Inner()
        self.rows = [Inner()]

    @readonly
    def rename(self) -> None:
        self.inner.name = "b"  # tpyc: error(/readonly/)


def main() -> None:
    Outer().rename()


main()
