# `na` starts `nb` before it defines E and F.
import nb


class E:
    v: int

    def __init__(self) -> None:
        self.v = 2


class F:
    w: int

    def __init__(self) -> None:
        self.w = 3
