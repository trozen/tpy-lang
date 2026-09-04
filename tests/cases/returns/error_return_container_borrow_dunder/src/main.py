# The adjacent shape to the container-operator return: a user `__add__` that
# returns a BORROW aliases an operand, so filling the by-value return slot
# from it is a copy the bare passthrough does not spell -- it keeps rejecting.
from tpy import Own, Int32


class Pool:
    xs: list[Int32]

    def __init__(self):
        self.xs = [1, 2]

    def __add__(self, other: 'Pool') -> list[Int32]:
        return self.xs


def borrowed(p: Pool, q: Pool) -> Own[list[Int32]]:
    return p + q  # tpyc: error(/return.container_source/)


def main():
    print(len(borrowed(Pool(), Pool())))


main()
