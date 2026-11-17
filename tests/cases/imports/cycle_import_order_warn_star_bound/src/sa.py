# `sa` starts `sb` before it defines E.
import sb


class E:
    v: int

    def __init__(self) -> None:
        self.v = 3
