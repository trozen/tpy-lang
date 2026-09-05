# The adjacent shape to the container-operator return: a user `__add__` that
# returns a BORROW aliases an operand, so filling the by-value return slot
# from it copies -- observably, since CPython would hand back the alias. The
# operator arm's `is_rvalue_source` test is what keeps the borrow-returning
# dunder out; the METHOD-call spelling of the same shape rejects beside it
# (error_return_container_borrow_method_own).
from tpy import Own, Int32


class Pool:
    xs: list[Int32]

    def __init__(self):
        self.xs = [1, 2]

    def __add__(self, other: 'Pool') -> list[Int32]:
        return self.xs


def borrowed(p: Pool, q: Pool) -> Own[list[Int32]]:
    return p + q  # tpyc: error(/return\.record_source\.TpyBinOp\.storage/)


def main():
    print(len(borrowed(Pool(), Pool())))


main()
