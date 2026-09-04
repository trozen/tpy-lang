# A container literal at an open-`T` element slot: an `Own[T]` param MOVES into
# the element (the literal switches to the reserve+emplace helper, since a
# brace-init's initializer_list elements are const and would copy), while a
# plain `T` param renders bare. `Cell` is @nocopy, so a silent copy on the move
# path would be a compile error rather than a wrong answer.
from tpy import Own, nocopy


@nocopy
class Cell:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


# The consume warning is spurious -- a container-literal element is not one of
# the consumption shapes sema counts, though the move does happen.
def wrap[T](a: Own[T]) -> Own[list[T]]:  # tpyc: warning(/never consumed/)
    return [a]      # tpyc: ok -- the moved element


def twice[T](a: T) -> Own[list[T]]:
    return [a, a]   # tpyc: ok -- a copyable `T` element renders bare


def main() -> None:
    cells = wrap(Cell(1))
    cells[0].n += 10        # the moved cell lives in the list
    print(cells[0].n, len(cells))
    print(len(twice(3)))


main()
