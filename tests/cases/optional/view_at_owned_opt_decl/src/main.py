# A str/bytes VALUE source bound into an owned-inner `Optional` local
# (`t: str | None = <src>`, where the slot is an `std::optional<std::string>`).
# The optional's converting ctor from a view is EXPLICIT, so a BORROW-form
# source constructs the inner first
# (`std::optional<std::string> t = std::string(s);`) while an OWNED source
# (a `str` field, whose member read is already `std::string`) lands bare.
# One verdict for the construct: the RETURN sink spells the same wrap, and the
# branch-first twin takes it through its predecl-plus-assign.
# The slot COPIES: every field section mutates the source field after the
# decl and prints `t`, which keeps the old text. str and bytes are value
# types, so that copy matches CPython's rebind-the-attribute semantics.
# Still a located reject: the whole decl inside a generator or `async def`
# (`res.local_storage` -- the frame has no owned-view optional local, for a
# view PARAM source just as much as for a field one), pinned by
# `error_gen_field_at_owned_opt_decl`.
from tpy import int32, StrView


class Inner:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Rec:
    name: str
    tag: bytes
    sv: StrView
    inner: Inner

    def __init__(self, name: str) -> None:
        self.name = name
        self.tag = b"bb"
        self.sv = "vv"
        self.inner = Inner(name)

    def label(self) -> str:
        return self.name + "!"


# free function, str param: a view source, so the inner takes the wrap
def str_param(s: str) -> int32:
    t: str | None = s  # tpyc: ok
    if t is None:
        return -1
    return len(t)


# the bytes twin: a `bytes` param is a BytesView at the signature
def bytes_param(b: bytes) -> int32:
    t: bytes | None = b  # tpyc: ok
    if t is None:
        return -1
    return len(t)


# a StrView local is a view source with no param indirection
def strview_local() -> int32:
    v: StrView = "abcdef"
    t: str | None = v  # tpyc: ok
    if t is None:
        return -1
    return len(t)


class Holder:
    name: str
    rec: Rec

    def __init__(self, name: str) -> None:
        self.name = name
        self.rec = Rec(name)

    # method position
    def tagged(self, s: str) -> int32:
        t: str | None = s  # tpyc: ok
        if t is None:
            return -1
        return len(t) + len(self.rec.name)

    # the field source read off `self` in a method
    def own_field(self) -> str:
        t: str | None = self.name  # tpyc: ok
        self.name = "mutated"
        if t is None:
            return "<none>"
        return t


# the branch-first twin of the same declaration
def branch_first(s: str, c: bool) -> int32:
    if c:
        t: str | None = s  # tpyc: ok
    else:
        t = None
    if t is None:
        return -1
    return len(t)


# an OWNED `str` FIELD read: the member is already `std::string`, so the
# slot takes it bare and the copy is what the mutation below exposes
def str_field(r: Rec) -> str:
    t: str | None = r.name  # tpyc: ok
    r.name = "mutated"
    if t is None:
        return "<none>"
    return t


# a CHAINED field read reaches the same slot through a record member
def chained_field(r: Rec) -> str:
    t: str | None = r.inner.name  # tpyc: ok
    r.inner.name = "mutated"
    if t is None:
        return "<none>"
    return t


# the receiver is a subscript, so the member read hangs off `__getitem__`
def field_off_subscript(rs: list[Rec]) -> str:
    t: str | None = rs[0].name  # tpyc: ok
    # the write goes through a bound element: an element RECEIVER is not a
    # view-family field-write target (records/error_str_field_write_elem_receiver)
    r = rs[0]
    r.name = "mutated"
    if t is None:
        return "<none>"
    return t


# the bytes family at the same slot: an owned `bytes` member read
def bytes_field(r: Rec) -> bytes:
    t: bytes | None = r.tag  # tpyc: ok
    r.tag = b"zz"
    if t is None:
        return b"<none>"
    return t


# a VIEW-typed field into the OWNED inner: this one takes the wrap
def strview_field(r: Rec) -> str:
    t: str | None = r.sv  # tpyc: ok
    r.sv = "mutated"
    if t is None:
        return "<none>"
    return t


# a str-returning METHOD CALL at the same slot (already admitted; pinned
# here so the field arm and the call arm stay side by side)
def call_source(r: Rec) -> str:
    t: str | None = r.label()  # tpyc: ok
    r.name = "mutated"
    if t is None:
        return "<none>"
    return t


# branch-first, field source: the predecl-plus-assign leg of each family
def branch_str_field(r: Rec, c: bool) -> str:
    if c:
        t: str | None = r.name  # tpyc: ok
    else:
        t = None
    r.name = "mutated"
    if t is None:
        return "<none>"
    return t


def branch_bytes_field(r: Rec, c: bool) -> bytes:
    if c:
        t: bytes | None = r.tag  # tpyc: ok
    else:
        t = None
    r.tag = b"zz"
    if t is None:
        return b"<none>"
    return t


def branch_strview_field(r: Rec, c: bool) -> str:
    if c:
        t: str | None = r.sv  # tpyc: ok
    else:
        t = None
    r.sv = "mutated"
    if t is None:
        return "<none>"
    return t


def main() -> None:
    print("str param:", str_param("abcd"))
    print("bytes param:", bytes_param(b"xyz"))
    print("strview local:", strview_local())
    print("method:", Holder("hello").tagged("ab"))
    print("branch first:", branch_first("abc", True), branch_first("abc", False))
    print("self field:", Holder("hello").own_field())
    print("str field:", str_field(Rec("hello")))
    print("chained field:", chained_field(Rec("hello")))
    print("field off subscript:", field_off_subscript([Rec("hello")]))
    print("bytes field:", bytes_field(Rec("hello")))
    print("strview field:", strview_field(Rec("hello")))
    print("call source:", call_source(Rec("hello")))
    print("branch str field:", branch_str_field(Rec("hello"), True),
          branch_str_field(Rec("hello"), False))
    print("branch bytes field:", branch_bytes_field(Rec("hello"), True),
          branch_bytes_field(Rec("hello"), False))
    print("branch strview field:", branch_strview_field(Rec("hello"), True),
          branch_strview_field(Rec("hello"), False))


main()
