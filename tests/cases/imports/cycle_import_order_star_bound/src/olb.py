# olb's own E, defined below the star import, is the one both CPython and TPy
# bind: the star import of the suspended `ola` copies nothing of it.
from ola import *  # tpyc: ok


def g() -> int:
    return E().v


class E:
    v: int

    def __init__(self) -> None:
        self.v = 3


def main() -> None:
    print("own late:", g())
