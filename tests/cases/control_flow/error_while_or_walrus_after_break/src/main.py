# A walrus behind `or` in a `while` head runs only on the path that leaves
# through the head -- the operands before it came out falsy. A `break` never
# takes that path, so after this loop the name is not assigned, which is what
# CPython answers with UnboundLocalError (`f(True)` below).
from tpy import int32


def probe(flag: bool) -> int32:
    i = 0
    while flag or (b := i + 1) > 0:
        i += 1
        break
    return b  # tpyc: error(/variable 'b' may not be assigned at this point/)


def main() -> None:
    print(probe(True))


main()
