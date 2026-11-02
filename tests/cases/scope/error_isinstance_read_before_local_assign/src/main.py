# An isinstance operand naming a class the function later rebinds to a value:
# the operand reads the function's local before it is bound (CPython's
# UnboundLocalError), never the module class.
from tpy import int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def main(v: P | int32) -> None:
    # The class read the later `P = 3` makes local.
    print(isinstance(v, P))  # tpyc: error(/'P' is read before it is bound on line 15: .* does not see the module's 'P'/)
    P = 3
    print(P)


main(P(1))
