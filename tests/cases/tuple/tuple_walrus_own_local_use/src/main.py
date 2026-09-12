# A walrus bound from an owning-tuple call declares the owning STORAGE form
# (an rvalue can't be address-lifted): local reads work, only the
# borrow-form return is rejected (error_tuple_walrus_own_return).
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def make_pair(v: int32) -> Own[tuple[int32, Box]]:
    return (v, Box(v))


def use() -> int32:
    if (t := make_pair(5))[0] > 0:
        return t[0] + t[1].val
    return 0


def use_branches(c: bool) -> int32:
    # The slot's (*t) read rewrite and storage-form classification are
    # function-scope (the pre-decl is hoisted), so reads in a SIBLING
    # branch of the walrus must see them too.
    if (t := make_pair(7))[0] > 0 and c:
        return t[0]
    else:
        return t[1].val


def main() -> None:
    print(use())
    print(use_branches(True))
    print(use_branches(False))


main()
