# A package __init__ in a cycle with its submodule: X is bound before the
# __init__ imports the submodule, so the submodule's `from pkg import X` works.
class X:
    def __init__(self, v: int) -> None:
        self.v = v


from pkg import sub  # noqa: E402


def main() -> None:
    x = X(1)
    sub.grow(x)
    print("pkg:", x.v)
