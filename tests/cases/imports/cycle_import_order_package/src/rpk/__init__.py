# The relative form of pkg's cycle: X is bound before the submodule runs.
class X:
    def __init__(self, v: int) -> None:
        self.v = v


from . import sub  # noqa: E402


def main() -> None:
    x = X(1)
    sub.grow(x)
    print("relative:", x.v)
