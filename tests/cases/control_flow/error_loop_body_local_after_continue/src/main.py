# A `continue` leaves the body clause just as a `break` does, so a binding
# BELOW one is not on every edge out of the body: the read after the loop is
# the reject CPython answers with UnboundLocalError. The head provably runs and
# the loop has no `else`, so the continue edge is the only thing deciding here.
from tpy import int32


def probe(flag: bool) -> int32:
    for i in range(3):
        if flag:
            continue
        w = i + 1
    return w  # tpyc: error(/variable 'w' may not be assigned at this point/)


def main() -> None:
    print(probe(True))


main()
