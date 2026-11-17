# `pa` starts `pb` before it defines E.
import pb


class E:
    v: int

    def __init__(self) -> None:
        self.v = 1
