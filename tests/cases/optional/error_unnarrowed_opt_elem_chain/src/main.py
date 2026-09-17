# An element chain off an UN-narrowed pointer-repr `Optional[container]` NAME:
# the hop renders the bare `(*d)` unwrap, which is keyed on the pointer
# BINDING set (a representation fact, not a proof), so without sema's
# narrowing the chain must stop here -- admitting it emitted
# `__getitem__(__getitem__((*d), 0).rows, 0)` with no null check while the
# warning on the same line promised one.
# The nested element-FIELD read is the representative sink; the same
# un-narrowed receiver rejects, located, at every other one (measured):
#   d[0].x += 1          stmt.aug_assign
#   d[0].rows.append(5)  method.recv.field_chain
#   d[0].rows[0] = 5     setitem.recv.field_chain
#   d[0].kids["a"] = 3   setitem.recv.field_chain
#   d[0].x = 5           assign.field_write_shape
#   len(d[0].rows)       call.native_arg.container
#   print(d[0].rows)     print.arg.container_field_access
#   p = d[0]             decl.slot_type
#   d[0][0]              subscript.recv.subscript
#   for v in d[0].rows   foreach.field_parent
# The one sink that still compiles is the scalar field read `d[0].x`, which
# is the separately filed BUGS.md#unnarrowed-optional-elem-field-read.
# The narrowed twins are happy sections of `optional/narrowed_opt_container_elem`.
from typing import Optional

from tpy import int32


class P:
    x: int32
    rows: list[int32]

    def __init__(self) -> None:
        self.x = 0
        self.rows = [1, 2, 3]


def first_row(d: Optional[list[P]]) -> int32:
    # the subject: a nested element read through an unproven Optional receiver
    return d[0].rows[0]  # tpyc: error(/not yet supported.*subscript.recv.field_chain/)


def main() -> None:
    print(first_row([P()]))


main()
