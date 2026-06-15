# BytesView sibling of error_explicit_view_temp: an explicit BytesView bound to a
# temporary is rejected (same reject path as StrView).
from tpy import BytesView


def make() -> bytes:
    return b"   padded long bytes that dodge the small buffer here   "


def from_owned_temp() -> None:
    v: BytesView = make()  # tpyc: error(/temporary view source/)
    print(len(v))


def main() -> None:
    from_owned_temp()


main()
