# An Optional local re-pointed at a borrow-returning dunder's result over a
# TEMPORARY operand (`-Cell(3)`) stays refused: the operand dies at the end
# of the statement, so the local would dangle. The lvalue-operand twin
# (`-b`) re-points (optional/opt_ptr_slot_reseat_shapes).
from tpy import int32


class Cell:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def __neg__(self) -> "Cell":
        return self


def neg_tmp(a: Cell | None) -> int32:
    cur = a  # tpyc: error(/decl.slot_type/)
    cur = -Cell(3)
    return 0 if cur is None else cur.value


print(neg_tmp(None))
