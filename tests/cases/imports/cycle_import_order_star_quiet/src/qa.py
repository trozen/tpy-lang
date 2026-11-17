# `qa` starts `qb` before it defines E.
import qb


class E:
    v: int

    def __init__(self) -> None:
        self.v = 2
