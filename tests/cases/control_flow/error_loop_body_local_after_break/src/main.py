# The same join with no `else` clause at all: a provable head runs the body,
# but the `break` leaves it before the binding below it, so the name is not
# assigned after the loop -- the read is the reject CPython answers with
# UnboundLocalError. The nested `def` is load-bearing: analyzing it saves and
# restores the whole function state, so a break collector captured by the loop
# arm rather than read off the live state would be orphaned here and this
# program would compile.
from tpy import int32


def probe(flag: bool) -> int32:
    for i in range(3):
        def inner() -> int32:
            return 1

        if flag:
            break
        w = i + inner()
    return w  # tpyc: error(/variable 'w' may not be assigned at this point/)


def main() -> None:
    print(probe(True))


main()
