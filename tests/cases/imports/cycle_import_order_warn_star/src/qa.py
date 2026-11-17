# `qa` assigns `__all__` only after it starts `qb`: CPython's star import in
# `qb` then binds the public names bound so far -- none.
import qb
__all__ = ["Q", "main"]


class Q:
    def __init__(self, v: int) -> None:
        self.v = v


def main() -> None:
    print("late __all__:", qb.make().v)
