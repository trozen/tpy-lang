# An UNPROVEN Optional intermediate in a field-chain subscript receiver: the
# link renders its own `deref_optional_check` inside the flat postfix chain,
# so the element read still names the inner container's storage and the
# access is checked at runtime (the warning says so). Narrowing the link
# first removes both the check and the warning.
from tpy import int32


class Inner:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]


class Outer:
    inner: Inner | None

    def __init__(self) -> None:
        self.inner = None


def unproven(o: Outer) -> None:
    # the checked form: the Optional link is not proven at this read
    print("unproven", o.inner.items[0])  # tpyc: warning(/Potential None access/)


def proven(o: Outer) -> None:
    if o.inner is not None:
        # the same chain with the link narrowed: no check, and the element
        # write lands in the inner container rather than in a copy
        o.inner.items[0] = 9  # tpyc: ok
        print("proven", o.inner.items[0])


def main() -> None:
    o = Outer()
    o.inner = Inner()
    unproven(o)
    proven(o)


main()
