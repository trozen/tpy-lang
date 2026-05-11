# For-loop iteration over `list[P | None]`: the loop var binds to a
# storage-form `optional<P>` element. Narrowing via `is not None` and
# field access still work via the storage-form-Optional source path
# (`deref_optional_check`).
from tpy import Int32


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def borrow(p: P | None) -> Int32:
    if p is None:
        return Int32(-1)
    return p.x


def main() -> None:
    pairs: list[P | None] = [P(Int32(1)), None, P(Int32(3))]
    # Direct access via narrowing
    for it in pairs:
        if it is not None:
            print(it.x)
        else:
            print(-1)
    # Pass loop var to a `P | None` borrow param -- consumer lifts via
    # optional_to_ptr at the call site.
    for it in pairs:
        print(borrow(it))


main()
