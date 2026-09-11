# A union with a BytesView member spells distinct alternatives, but the
# widened union member class admits str / bytes / Span and not the bytes VIEW,
# so its isinstance arm rejects at a located tag where `str | StrView` lowers.
# BUGS.md#bytesview-union-member-rejects
from tpy import Int32, BytesView


def bytes_or_view(u: bytes | BytesView) -> Int32:
    if isinstance(u, BytesView):  # tpyc: error(/cond.call/)
        return len(u)
    return 0


def main() -> None:
    print(1)


main()
