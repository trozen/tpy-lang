# A module whose function a lambda calls through the module name.
from tpy import int32


class Q:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def same_q(q: Q) -> Q:
    return q
