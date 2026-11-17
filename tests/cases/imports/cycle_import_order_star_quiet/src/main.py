# Cycle peers CPython imports as TPy does, with no warning: a name a star
# import copies from a compound statement before the cycle import, and a
# reader's own definition above a star import of a peer that defines the
# same name later.
import wc
import wa
import wb
import qa
import qb


def main() -> None:
    print("uncertain:", wb.f())
    qb.main()


main()
