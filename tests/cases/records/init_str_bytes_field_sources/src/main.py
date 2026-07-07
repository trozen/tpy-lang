# Ctor member-init-list str/StrView/bytes field sources: str/bytes literals,
# a StrView param into a str field (std::string(v) materialization), a StrView
# field from param/literal, and the bytes() empty-ctor rvalue. Copy semantics
# at the ctor boundary are intended (str/bytes are immutable in CPython).
from tpy import StrView


class Meta:
    title: str
    tag: str
    view: StrView
    label: StrView

    def __init__(self, title: str, v: StrView):
        self.title = v
        self.tag = "fixed"
        self.view = title
        self.label = "lit"


class Buf:
    data: bytes
    lit: bytes
    empty: bytes

    def __init__(self, data: bytes):
        self.data = data
        self.lit = b"\x01\x02"
        self.empty = bytes()


def main():
    m = Meta("hello", "world")
    print(m.title, m.tag, m.view, m.label)
    b = Buf(b"xyz")
    print(len(b.data), len(b.lit), len(b.empty))
    print(b.data[0], b.lit[1])


main()
