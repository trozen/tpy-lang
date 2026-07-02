# An owned-string return bound to an inferred local must promote to an owned copy
# (not a dangling view) -- and this must be identical across a free function, a
# method, and a @property (the property was the outlier the fix aligned). A getter
# returning a StrView borrow of a stable field stays a zero-copy view in all forms.
from tpy import StrView


def free_text(b: bytes) -> str:       # free function, owned return
    return b.decode()


class Box:
    payload: bytes
    label: str

    def __init__(self, payload: bytes, label: str) -> None:
        self.payload = payload
        self.label = label

    def m_text(self) -> str:          # method, owned return
        return self.payload.decode()

    def m_name(self) -> StrView:      # method, borrow of the stable field
        return self.label

    @property
    def text(self) -> str:            # property, owned return
        return self.payload.decode()

    @property
    def tagged(self) -> str:          # property, owned concatenation
        return self.label + "!"

    @property
    def name(self) -> StrView:        # property, borrow of the stable field
        return self.label

    @property
    def raw(self) -> bytes:           # property, owned bytes (decode round-trip)
        return self.text.encode()


def main() -> None:
    b = Box(b"a payload well beyond the sixteen byte small-string buffer here", "a-stable-field-label")
    # owned returns -- function, method, property must all own the local
    f = free_text(b.payload)          # tpyc: type(str)
    print(f)
    m = b.m_text()                    # tpyc: type(str)
    print(m)
    t = b.text                        # tpyc: type(str)
    print(t)
    print(len(t))
    g = b.tagged                      # tpyc: type(str)
    print(g)
    # borrow returns -- method and property both stay zero-copy views
    mn = b.m_name()                   # tpyc: type(StrView)
    print(mn)
    n = b.name                        # tpyc: type(StrView)
    print(n)
    r = b.raw                         # tpyc: type(bytes)
    print(len(r))


main()
