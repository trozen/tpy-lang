# A comprehension at a NESTED field target (`p.inner.data = ...`): the
# container field-write receiver admits one field off a name, so the write
# keeps rejecting here -- for every container source, not only the
# comprehension (BUGS.md#nested-field-target-container-write) -- while the
# one-level target lowers (field_write_comprehension).
from tpy import int32


class Inner:
    data: list[list[int32]]

    def __init__(self) -> None:
        self.data = []


class Pic:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner()


def fill(p: Pic, n: int32) -> None:
    p.inner.data = [[j] for j in range(n)]  # tpyc: error(/not yet supported/)


def main() -> None:
    p = Pic()
    fill(p, 3)
    p.inner.data[0].append(7)
    print(p.inner.data)


main()
