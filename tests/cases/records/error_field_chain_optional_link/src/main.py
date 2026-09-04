# An OPTIONAL intermediate in a field-chain subscript receiver: that link
# renders a deref/unwrap the flat postfix chain does not carry, so the
# chain row must keep rejecting rather than spell a bare member read.
from tpy import Int32


class Inner:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]


class Outer:
    inner: Inner | None

    def __init__(self) -> None:
        self.inner = None


def main() -> None:
    o = Outer()
    o.inner = Inner()
    print(o.inner.items[0])  # tpyc: error(/subscript.recv.field_chain/)


main()
