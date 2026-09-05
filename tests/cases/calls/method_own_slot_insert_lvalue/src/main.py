# The direction `_x_insert_own_slot` keeps open: an LVALUE at a builtin
# container's `Own[T]` insert slot. The enum leg takes the const-ref overload
# of `push_back` (no temp, no move); the bytes leg materializes the owned
# copy first (`bytes_copy`) and hands that prvalue to the rvalue overload.
# The same cells reject at a user record's `Own[T]` slot
# (calls/error_method_own_slot_element_read).
from enum import Enum


class Color(Enum):
    RED = 1
    BLUE = 2


def main() -> None:
    blobs: list[bytes] = []
    data = b"pq"
    # A bytes NAME at `list[bytes].append`'s Own[bytes] slot -- the
    # `bytes_owned_name` cell; `data` still reads its own value afterwards.
    blobs.append(data)  # tpyc: ok
    picks: list[Color] = []
    c = Color.BLUE
    # An enum NAME at the same slot shape -- the `own_enum_elem` cell.
    picks.append(c)  # tpyc: ok
    print(len(blobs), len(data), picks[0] is c)


main()
