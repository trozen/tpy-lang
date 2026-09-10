# io read(size): StringIO/BytesIO bounded reads + Readable/BinaryReadable
# protocol params + the native TextIO/BinaryIO file read(size) path.
import io
from tpy import Readable, BinaryReadable


def stringio_read_size() -> None:
    s = io.StringIO("hello world")
    print("n5:", s.read(5))                  # hello
    print("n3:", s.read(3))                  #  wo
    print("rest:", s.read())                 # rld
    print("eof:", "[" + s.read(5) + "]")     # []
    s.seek(0)
    print("past-end:", s.read(100))          # whole string (clamped)
    s.seek(0)
    print("zero:", "[" + s.read(0) + "]")    # []
    print("after-zero:", s.read())           # still whole string


def bytesio_read_size() -> None:
    b = io.BytesIO(b"abcdef")
    print("n3:", b.read(3))                  # b'abc'
    print("rest:", b.read())                 # b'def'
    print("eof:", b.read(2))                 # b''
    b.seek(0)
    print("zero:", b.read(0))                # b''
    print("neg:", b.read(-1))                # whole (negative = all)


def via_protocol(fp: Readable) -> str:
    # Sequential reads, not `read(4) + read()`: TPy evaluates a single binary
    # op's operands right-to-left, so the inline form would reorder the reads.
    head = fp.read(4)
    rest = fp.read()
    return head + "|" + rest


def via_binary_protocol(fp: BinaryReadable) -> bytes:
    return fp.read(2)


def protocol_params() -> None:
    print("proto-text:", via_protocol(io.StringIO("abcdefgh")))     # abcd|efgh
    print("proto-bytes:", via_binary_protocol(io.BytesIO(b"xyz")))  # b'xy'


def file_read_size() -> None:
    tpath = "tpy_test_io_read_size.txt"
    with open(tpath, "w") as f:
        f.write("abcdefghij")
    with open(tpath) as r:
        print("file-zero:", "[" + r.read(0) + "]")  # []
        print("file-n4:", r.read(4))             # abcd
        print("file-eq-remaining:", r.read(6))   # efghij (size == bytes left)
        print("file-rest:", r.read())            # "" (already at EOF)
        print("file-eof:", "[" + r.read(5) + "]")  # []

    bpath = "tpy_test_io_read_size.bin"
    with open(bpath, "wb") as bf:
        bf.write(b"0123456789")
    with open(bpath, "rb") as br:
        print("bfile-n4:", br.read(4))           # b'0123'
        print("bfile-rest:", br.read())          # b'456789'
        print("bfile-eof:", br.read(3))          # b''


def main() -> None:
    stringio_read_size()
    print("---")
    bytesio_read_size()
    print("---")
    protocol_params()
    print("---")
    file_read_size()


main()
