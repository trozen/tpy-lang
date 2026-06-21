# A last-use copyable reference local in a set LITERAL routes through the
# move / make_ordered_set path. Set elements must be copy-constructible, so
# @nocopy can't force the move; a frozen @dataclass (hashable, copyable)
# exercises the path with a real reference-type element.
from dataclasses import dataclass
from tpy import Int32


@dataclass(frozen=True)
class P:
    x: Int32


def main() -> None:
    a = P(1)
    b = P(2)
    s = {a, b}  # last use of a, b -> moved into the set
    print(len(s))


main()
