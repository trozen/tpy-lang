"""A view-family FIELD read into a resumable frame field of the same family:
str and bytes share the bare member read, so the frame write threads the same
field admission for both. The mutable sibling (`bytearray`) is a reference
type and keeps its reject."""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _reject_tally,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)


def _codegen_facts(source: str):
    """(face witnesses, fallback tally) from a full THIR codegen run -- the
    resumable leaf pass only runs there."""
    compiler, modules = _compile(source)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=True,
                               comment_line_numbers=False))
    return compiler._thir_face_witnesses


BYTES_FIELD = """
from tpy import Int32
from typing import Iterator


class Holder:
    blob: bytes

    def __init__(self, blob: bytes) -> None:
        self.blob = blob

    def chunks(self, size: Int32, alt: bool) -> Iterator[bytes]:
        if alt:
            yield b"alt"
        else:
            data = self.blob
            n = len(data)
            pos: Int32 = 0
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
"""


def test_bytes_field_into_frame_field_routes():
    faces = _codegen_facts(BYTES_FIELD)
    assert faces.get("print.bytes_field", 0) >= 1


def test_bytes_field_into_frame_field_byte_identical():
    _hpp, cpp = _assert_routes_byte_identical(BYTES_FIELD)
    assert "data = __self.blob;" in cpp


# The landed str sibling, kept as a companion so a regression on either half
# of the shared row is visible here.
STR_FIELD = """
from tpy import Int32
from typing import Iterator


class Holder:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def parts(self, alt: bool) -> Iterator[str]:
        if alt:
            yield "alt"
        else:
            text = self.label
            n = len(text)
            pos: Int32 = 0
            while pos < n:
                yield text[pos:pos + 2]
                pos = pos + 2


def main() -> None:
    h = Holder("abcdef")
    for p in h.parts(False):
        print(p)


main()
"""


def test_str_field_into_frame_field_routes():
    faces = _codegen_facts(STR_FIELD)
    assert faces.get("fstr.str_field", 0) >= 1


# A BytesView field is the borrow-form half of the same family: the member
# read is bare there too, so it rides the same row.
BYTESVIEW_FIELD = """
from tpy import Int32, BytesView
from typing import Iterator


class Holder:
    blob: BytesView

    def __init__(self, blob: BytesView) -> None:
        self.blob = blob

    def chunks(self, size: Int32, alt: bool) -> Iterator[bytes]:
        if alt:
            yield b"alt"
        else:
            data = self.blob
            n = len(data)
            pos: Int32 = 0
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
"""


def test_bytesview_field_into_frame_field_routes():
    fallback = _reject_tally(BYTESVIEW_FIELD)
    # The generator body routes; the ctor's member-init of a BytesView field
    # is an unrelated gap in the member-init ladder, spelled out so this stays
    # an exact whole-dict claim rather than a prefix filter.
    assert fallback == {"ctor:ctor.mil_field.nominal.name": 1}


# Boundary: `bytearray` is a REFERENCE type, so the frame slot is not a
# view-family value and the bare member read is not its render -- the write
# must keep rejecting rather than ride the bytes row.
BYTEARRAY_FIELD = """
from tpy import Int32
from typing import Iterator


class Holder:
    buf: bytearray

    def __init__(self, n: Int32) -> None:
        self.buf = bytearray(n)

    def chunks(self, size: Int32, alt: bool) -> Iterator[Int32]:
        if alt:
            yield -1
        else:
            data = self.buf
            n = len(data)
            pos: Int32 = 0
            while pos < n:
                yield data[pos]
                pos = pos + size


def main() -> None:
    h = Holder(4)
    for c in h.chunks(2, False):
        print(c)


main()
"""


def test_bytearray_field_into_frame_field_stays_ast():
    fallback = _reject_tally(BYTEARRAY_FIELD)
    # The ctor's `bytearray(n)` member-init routes (the type constructor is
    # target-threaded); the GENERATOR body is the boundary this pins -- the
    # bare member read binds a reference the frame cannot hold.
    assert fallback == {"resumable:res.alias_bind": 1}


# Boundary at the gate itself: the slot family opens the row, but the FIELD
# READ still has to pass the field ladder's receiver shapes -- a call receiver
# does not, so the write keeps rejecting.
BYTES_FIELD_CALL_RECV = """
from tpy import Int32, Own
from typing import Iterator


class Holder:
    blob: bytes

    def __init__(self, blob: bytes) -> None:
        self.blob = blob


def make() -> Own[Holder]:
    return Holder(b"abcdef")


def gen(alt: bool) -> Iterator[Int32]:
    if alt:
        yield -1
    else:
        data = make().blob
        n = len(data)
        pos: Int32 = 0
        while pos < n:
            yield data[pos]
            pos = pos + 1


def main() -> None:
    for v in gen(False):
        print(v)


main()
"""


def test_bytes_field_off_call_receiver_stays_ast():
    fallback = _reject_tally(BYTES_FIELD_CALL_RECV)
    _assert_rejects_at(fallback, "resumable:field.result_type")
