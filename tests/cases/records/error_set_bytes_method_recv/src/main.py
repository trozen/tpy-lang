# `set[bytes]` has no method surface at all (BUGS.md#set-bytes-no-method-surface):
# `s.add(...)` rejects for a bytes
# LITERAL exactly as it does for the member read below, so this is the receiver
# family, not the value. The sibling containers all take the member read now
# (`list[bytes].append` / `.insert`, the container literal, the comprehension
# element, the subscript-assign key and value -- see
# `records/viewfam_field_at_owning_sinks`), and so does the `str` twin
# `set[str].add(i.name)`. `_set_method_recv` leaves an owned-`bytes` element
# out deliberately: admitting it also admits `set[bytes].discard(b"x")`, whose
# arg row renders a view literal into an owned-vector slot and fails the C++
# build -- the runtime signature is the thing to fix.
from tpy import Own


class Inner:
    def __init__(self, tag: Own[bytes]) -> None:
        self.tag = tag


def main() -> None:
    i = Inner(b"t")
    s: set[bytes] = set()
    s.add(i.tag)  # tpyc: error(/method.set.add/)
    print(len(s))


main()
