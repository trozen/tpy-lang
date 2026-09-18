# A `break` jumps past the `else` clause, so a name bound only there is NOT
# assigned after a loop whose body can break -- the read below is the reject
# CPython answers with UnboundLocalError. The hoisted flavor: `w` is also
# bound in the body, so before the join landed this compiled to a read of the
# slot the break path left uninitialized. The generator and async spellings of
# the same shape reject here too (the join is a sema assignment fact, not a
# frame one).
from tpy import int32


def probe(flag: bool) -> int32:
    for i in range(3):
        if flag:
            break
        w = i
    else:
        w = 9
    return w  # tpyc: error(/variable 'w' may not be assigned at this point/)


def main() -> None:
    print(probe(True))


main()
