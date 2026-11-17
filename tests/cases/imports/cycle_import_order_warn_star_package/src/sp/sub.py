# `sp` has finished; `sp.sub` starts `spb` before it is bound on `sp`.
class E:
    v: int

    def __init__(self) -> None:
        self.v = 1


import spb  # noqa: E402
