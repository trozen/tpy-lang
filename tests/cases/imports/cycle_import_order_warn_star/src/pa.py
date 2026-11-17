# `pa` starts `pb` before it defines anything `pb` star-imports.
import pb


class E(Exception):
    pass


class F:
    def __init__(self, v: int) -> None:
        self.v = v


def main() -> None:
    print("star:", pb.use(), pb.use2(F(7)))
