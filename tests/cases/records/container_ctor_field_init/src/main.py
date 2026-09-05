# A reference-typed FIELD initialized in a constructor. The member-init list
# is a target-threaded position -- the FIELD type is the render target -- so
# the three `bytearray` constructor spellings land in the slot the way the
# other containers' do (the `list()` leg beside them is the same row), and a
# same-typed param copies bare into it.
#
# The `Copied` leg's store is a COPY on purpose: storing a reference type into
# owned storage copies and warns, which is what the annotation pins. That is a
# declared divergence from CPython, where the field would alias the caller's
# buffer, so the case does not mutate through the field afterwards -- doing so
# would make the two runs print different lengths.
from tpy import Int32


class Empty:
    buf: bytearray
    tags: list[Int32]

    def __init__(self) -> None:
        self.buf = bytearray()  # tpyc: ok
        self.tags = list()  # tpyc: ok


class Seeded:
    buf: bytearray

    def __init__(self, seed: bytes) -> None:
        self.buf = bytearray(seed)  # tpyc: ok


class Sized:
    buf: bytearray

    def __init__(self, n: Int32) -> None:
        self.buf = bytearray(n)  # tpyc: ok


class Copied:
    buf: bytearray

    def __init__(self, data: bytearray) -> None:
        # Storing a reference type into owned storage copies, and says so.
        self.buf = data  # tpyc: warning(/copies bytearray into field/)


def main() -> None:
    e = Empty()
    e.buf.append(65)
    e.tags.append(1)
    print(len(e.buf))
    print(len(e.tags))
    s = Seeded(b"abc")
    s.buf.append(66)
    print(len(s.buf))
    z = Sized(3)
    print(len(z.buf))
    print(z.buf[0])
    src = bytearray(b"xy")
    c = Copied(src)
    print(len(c.buf))


main()
