# `ola` starts `olb` before it defines its E.
import olb


class E:
    v: int

    def __init__(self) -> None:
        self.v = 2
