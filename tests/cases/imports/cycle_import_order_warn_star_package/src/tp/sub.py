# `tp.sub` starts `tpb` while `tp` is still being imported.
class E:
    v: int

    def __init__(self) -> None:
        self.v = 2


import tpb  # noqa: E402
