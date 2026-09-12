# Comparing an un-narrowed `char | None` with a str literal: the literal is
# target-typed to the optional's char inner, so the compare answers per member.
from tpy import char


def eq(o: char | None) -> bool:
    return o == "a"


def main() -> None:
    print(eq(char("a")), eq(char("b")), eq(None))


main()
