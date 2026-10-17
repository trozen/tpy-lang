# A consuming method on a comprehension variable over a borrowed list copies the
# receiver, which a @nocopy element has no copy for (LANGUAGE_FEATURES "@nocopy Types").
from typing import Self
from tpy import Own, int32, nocopy


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def finish(self: Own[Self]) -> Own[Self]:
        return self


def finish_all(cs: list[Cell]) -> Own[list[Cell]]:
    return [c.finish() for c in cs]  # tpyc: error(/@nocopy type 'Cell' cannot be passed as the consuming receiver Own\[Cell\]/)


def main() -> None:
    # Built by appends: BUGS.md#literal-local-array-at-container-param-build.
    cs: list[Cell] = []
    cs.append(Cell(1))
    print(len(finish_all(cs)))


main()
