# Comparing an un-narrowed `Char | None` with a str literal: the literal is
# target-typed to the optional's Char inner, so the compare answers per member.
from tpy import Char


def eq(o: Char | None) -> bool:
    return o == "a"


def main() -> None:
    print(eq(Char("a")), eq(Char("b")), eq(None))


main()
