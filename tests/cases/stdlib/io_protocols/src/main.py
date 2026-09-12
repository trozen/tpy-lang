# Confirm StringIO/BytesIO conform to the io.Writable / io.Readable
# protocols, and that consumer functions parameterized by those protocols
# accept both in-memory buffers and the print-target shapes.
import io
from tpy import (
    int32,
    Writable, Readable, BinaryWritable, BinaryReadable,
    Seekable, Closable,
)


def emit_text(fp: Writable, items: list[str]) -> None:
    for s in items:
        fp.write(s)
        fp.write("\n")


def consume_text(fp: Readable) -> str:
    return fp.read()


def emit_bytes(fp: BinaryWritable, chunks: list[bytes]) -> None:
    for b in chunks:
        fp.write(b)


def consume_bytes(fp: BinaryReadable) -> bytes:
    return fp.read()


def rewind_and_close(fp: Seekable) -> int32:
    pos_before = fp.tell()
    fp.seek(int32(0))  # Defaults declared in the protocol method propagate.
    return pos_before


def closer(fp: Closable) -> None:
    fp.close()


def main() -> None:
    sink = io.StringIO()
    emit_text(sink, ["alpha", "beta", "gamma"])
    print("sink-text:", sink.getvalue())

    src = io.StringIO("first\nsecond\n")
    print("src-text:", consume_text(src))

    bsink = io.BytesIO()
    emit_bytes(bsink, [b"abc", b"def"])
    print("bsink-bytes:", bsink.getvalue())

    bsrc = io.BytesIO(b"xyz123")
    print("bsrc-bytes:", consume_bytes(bsrc))

    s2 = io.StringIO("seek-test")
    s2.read()  # advance to end
    print("rewind-from:", rewind_and_close(s2))
    print("after-rewind read:", s2.read())

    s3 = io.StringIO("close-me")
    closer(s3)
    print("closed-after-protocol:", s3.closed)


main()
