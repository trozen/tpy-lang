# A `BytesView` field initialized from the constructor's parameter: a field
# write (member-init or method alike) has no source form for the view family,
# even though the generator reading that field routes, so it rejects.
from typing import Iterator
from tpy import BytesView, int32


class Holder:
    blob: BytesView

    def __init__(self, blob: BytesView) -> None:
        # The view parameter is the member-init source.
        self.blob = blob  # tpyc: error(/field_write\.lift\.borrow/)

    def chunks(self, size: int32, alt: bool) -> Iterator[bytes]:
        if alt:
            yield b"alt"
        else:
            data = self.blob
            n = len(data)
            pos: int32 = 0
            while pos < n:
                end = pos + size
                if end > n:
                    end = n
                yield data[pos:end]
                pos = end


def main() -> None:
    h = Holder(b"abcdef")
    for c in h.chunks(2, False):
        print(len(c))


main()
