# A VALUE element read out of a reference-typed global is COPIED into the
# tuple, so the rebound-global borrow rule must leave it alone and the reject
# must land where it belongs -- on the rebind itself, exactly as it does for
# the scalar `return XS[0]` twin. `head` is defined first on purpose: before
# the element check was moved behind the borrow-form filter, the tuple return
# claimed "Cannot return a borrow of module variable 'XS' ... Use Own[int32]",
# advice no one can follow.
from tpy import int32

XS = [1, 2, 3]


def head() -> tuple[int32, int32]:
    return (XS[0], 1)


def reset() -> None:
    global XS
    XS = [9]  # tpyc: error(/Cannot reassign global variable 'XS' of non-value type/)


def main() -> None:
    a, b = head()
    reset()
    print(a, b)


main()
