# bytes.strip() now returns a BytesView (a view of the receiver), matching
# str.strip -> StrView, instead of an owned copy. So returning the strip of a
# local bytes dangles and is rejected -- proof that bytes.strip is a
# receiver-borrowing view covered by the view-lifetime machinery. Use an owned
# `bytes` annotation to keep a copy.
from tpy import BytesView


def make() -> bytes:
    return b"   padded long bytes that dodge the small buffer here   "


def trimmed() -> BytesView:
    b = make()
    return b.strip()  # tpyc: error(/local or temporary/)


def main() -> None:
    print(len(trimmed()))


main()
