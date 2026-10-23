# A `finally` the `break` unwinds through binds the name only conditionally,
# so after the loop it may not be assigned (CPython: UnboundLocalError).
from tpy import int32


def probe(flag: bool) -> int32:
    while True:
        try:
            break
        finally:
            if flag:
                w = 5
    # The subject: `w` is bound only when `flag` held.
    return w  # tpyc: error(/variable 'w' may not be assigned at this point/)


def main() -> None:
    print(probe(False))


main()
