# A `print(file=...)` sink read out of a container: the sink list is built from
# module-variable record reads (`[sys.stdout, sys.stderr]` -- each pointer slot
# copy-initializes its by-value element, which is what a container of records
# holds, and which sema warns about) and the element read goes under the pinned
# as_ostream consumer. The copy is harmless here: a StdStream is a non-owning
# handle, so writing through a copy writes to the same stream, which is what the
# stderr pick observes. stderr output is not captured by the runner, so only the
# stdout picks show up. The pinned warning text names the private
# `_StdStream` and not `sys.stdout` --
# BUGS.md#copy-warning-names-private-stdlib-type -- so the annotations below
# change when that is fixed.
import sys
from tpy import int32


# free function: a runtime index into the sink list.
def emit(n: int32, which: int32) -> None:
    outs = [sys.stdout, sys.stderr]  # tpyc: warning(/copies _StdStream/) warning(/copies _StdStream/)
    print(n, file=outs[which])  # tpyc: ok


# free function: a literal index, so the read is bounds-proven.
def emit_first(n: int32) -> None:
    outs = [sys.stdout, sys.stderr]  # tpyc: warning(/copies _StdStream/) warning(/copies _StdStream/)
    print(n, file=outs[0])  # tpyc: ok


# free function: a single-element sink list, the same element read.
def emit_only(n: int32) -> None:
    outs = [sys.stdout]  # tpyc: warning(/copies _StdStream/)
    print(n, file=outs[0])  # tpyc: ok


def main() -> None:
    emit(1, 0)
    emit(2, 1)
    emit_first(3)
    emit_only(4)


main()
