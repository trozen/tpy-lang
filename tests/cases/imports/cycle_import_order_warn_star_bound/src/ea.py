# `ea` starts `eb` before it defines E.
import eb


class E:
    v: int

    def __init__(self) -> None:
        self.v = 2
