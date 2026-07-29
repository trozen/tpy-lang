# A docstring emits no code, but the `#` comments preceding it still reach the
# generated C++ -- at module, function, constructor and method level.
"""Module docstring, preceded by the comment block above."""
from tpy import Int32


def free_fn() -> Int32:
    # comment before a free-function docstring
    """Free function docstring."""
    return 1


def skipped() -> None:
    # `pass` is the boundary: unlike a docstring it keeps its own source line
    # on top of the trivia.
    pass


class Counter:
    # the other boundary: a class-body docstring emits nothing at all, so this
    # comment must not reach the generated C++ either.
    """Class docstring."""

    n: Int32

    def __init__(self) -> None:
        # comment before a constructor docstring
        """Constructor docstring."""
        self.n = 0

    def bump(self) -> Int32:
        # comment before a method docstring
        """Method docstring."""
        self.n += 1
        return self.n


def main() -> None:
    c = Counter()
    skipped()
    print(free_fn() + c.bump() + c.bump())


main()
