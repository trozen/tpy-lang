# An element WRITE off an UN-narrowed pointer-repr `Optional[container]`
# NAME still rejects: the reads through such a receiver render the checked
# unwrap (`::tpy::deref_check(d)`, see `optional/unproven_opt_container_elem`),
# but the write sinks keep their own located rejects.
# The aug-assign of an element field is the representative sink; the same
# un-narrowed receiver rejects, located, at every other one (measured):
#   d[0].rows.append(5)  method.recv.field_chain
#   d[0].m()             method.recv.subscript
#   d[0].rows[0] = 5     setitem.recv.field_chain
#   d[0].kids["a"] = 3   setitem.recv.field_chain
#   d[0].x = 5           assign.field_write_shape
#   len(d[0].rows)       call.native_arg.container
#   print(d[0].rows)     print.arg.container_field_access
#   p = d[0]             decl.slot_type
#   for v in d[0].rows   foreach.iter_borrow_unplaceable
# The narrowed twins are happy sections of `optional/narrowed_opt_container_elem`.
# Filed as BUGS.md#unproven-optional-elem-sink-rejects; the fix plan is
# TODO.md "Widen the remaining flag-keyed rejects over an UNPROVEN `Optional`
# container receiver".
from typing import Optional

from tpy import int32


class P:
    x: int32
    rows: list[int32]

    def __init__(self) -> None:
        self.x = 0
        self.rows = [1, 2, 3]


def bump(d: Optional[list[P]]) -> None:
    # the subject: an element-field write through an unproven Optional receiver
    d[0].x += 1  # tpyc: error(/not yet supported.*stmt.aug_assign/)


def main() -> None:
    d = [P()]
    bump(d)
    print(d[0].x)


main()
