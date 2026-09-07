# A BytesView parameter at a `bytes` field: the view-to-owned coerce is the copy
# render, so the field owns its buffer.
from tpy import BytesView


class Blob:
    data: bytes

    def __init__(self, v: BytesView) -> None:
        self.data = v  # bytesview_to_bytes -> an owned copy


def main() -> None:
    b = Blob(b"abc")
    print(len(b.data), b.data[0])


main()
