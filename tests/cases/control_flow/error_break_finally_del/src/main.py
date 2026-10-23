# A `break` leaves after the `finally` it unwinds through, with what that
# body's analysis found: a name it binds and then deletes is not bound.
from tpy import int32


def probe(flag: bool) -> int32:
    while True:
        try:
            if flag:
                break
        finally:
            w = 5
            del w
    # The subject: `w` was deleted on the way out.
    return w  # tpyc: error(/variable 'w' may not be assigned at this point/)


print(probe(True))
