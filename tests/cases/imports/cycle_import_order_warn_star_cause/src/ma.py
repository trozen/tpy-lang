# `ma` starts `mb` before it defines E and F.
import mb


class E:
    v: int

    def __init__(self) -> None:
        self.v = 2


class F:
    w: int

    def __init__(self) -> None:
        self.w = 3
