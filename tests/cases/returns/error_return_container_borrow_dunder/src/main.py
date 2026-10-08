# A user `__add__` that returns a BORROW aliases an operand, so filling the
# by-value `Own[list[int32]]` return slot from it copies -- sema says so with
# the same warning every owning slot emits (the RECORD payload is
# warn_return_record_borrow_dunder, which lowers). This one stays an `error_`
# case because the CONTAINER-payload dunder has no render at that slot in
# EITHER spelling -- `copy(p + q)` rejects at the same place
# (BUGS.md#dunder-borrow-container-own-return), so the case pins that reject
# -- the record slice the binop result gate keeps (no row binds the
# container alias the dunder declares readonly).
# Only the reject: the harness replaces accumulated warnings with the error
# text when a CompileError follows, so the warning cannot be annotated here.
from tpy import Own, int32


class Pool:
    xs: list[int32]

    def __init__(self):
        self.xs = [1, 2]

    def __add__(self, other: 'Pool') -> list[int32]:
        return self.xs


def borrowed(p: Pool, q: Pool) -> Own[list[int32]]:
    return p + q  # tpyc: error(/binop.shape/)


def main():
    print(len(borrowed(Pool(), Pool())))


main()
