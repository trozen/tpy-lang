# A local bound from a call returning OWNING tuple storage is fine to USE
# locally (reads, element access, storage copies) -- only the borrow-form
# RETURN of it is rejected (error_tuple_own_call_return).
from tpy import Int32, Own


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def make_pair(v: Int32) -> Own[tuple[Int32, Box]]:
    return (v, Box(v))


def use() -> Int32:
    t = make_pair(5)
    return t[0] + t[1].val


def main() -> None:
    print(use())


main()
