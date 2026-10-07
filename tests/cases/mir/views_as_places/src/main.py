# MIR pins for views as places: StrView / BytesView locals and their sources
# (a parameter, a slice, a literal, an owned local, a field, a call), view
# returns and their origins, String bound as str, owned-leaf record fields and
# constructor member-init, and the first str conflicts. Analysis-only: the
# pins are MIR verdicts and line facts, the generated C++ is what sema's view
# rule already produced.
from tpy import BytesView, Span, StrView, String, int32


def takes(s: str) -> int:
    return len(s)


# free function: a view parameter bound to locals -- every alias borrows the parameter
def view_param(s: str) -> int:  # tpyc: mir(covered)
    v = s  # tpyc: mir_borrowed(v) mir_borrows(v, s)
    w = v  # tpyc: mir_borrowed(w) mir_borrows(w, s)
    return len(w)


# free function: an unstepped slice is a borrow of the whole buffer
def slice_local(s: str) -> int:  # tpyc: mir(covered)
    v = s[1:3]  # tpyc: mir_borrowed(v) mir_borrows(v, s)
    return len(v)


# free function: computed bounds are read before the borrow
def slice_bounds(s: str, a: int32, b: int32) -> int:  # tpyc: mir(covered)
    v = s[a:b]  # tpyc: mir_borrows(v, s)
    return len(v)


# free function: a slice of an owned local borrows that local
def slice_of_owned(a: str, b: str) -> int:  # tpyc: mir(covered)
    t = a + b  # tpyc: mir_owned(t)
    v = t[1:]  # tpyc: mir_borrowed(v) mir_borrows(v, t)
    return len(v)


# free function: a literal is a static origin
def literal_view() -> int:  # tpyc: mir(covered)
    v = "hello"  # tpyc: mir_borrowed(v) mir_borrows(v, static)
    return len(v)


# free function: a view result borrows the parameter; the summary says which
def view_return(s: str) -> StrView:  # tpyc: mir(covered) mir_summary(known)
    return s[1:]


# free function: the caller's holder takes the callee's origins
def view_return_caller(s: str) -> int:  # tpyc: mir(covered)
    v = view_return(s)  # tpyc: mir_borrowed(v) mir_borrows(v, s)
    return len(v)


# free function: a view at an owned sink copies through the view
def owned_from_view(s: str) -> str:  # tpyc: mir(covered)
    v = s[1:]  # tpyc: mir_borrowed(v)
    return v


# free function: a view parameter is a loan passed by value
def explicit_view_param(v: StrView) -> StrView:  # tpyc: mir(covered) mir_summary(known)
    return v


# free function: a String lent to a str parameter is a borrow, not a copy
def string_as_str_param(x: String) -> int:  # tpyc: mir(covered)
    return takes(x)


# free function: a String at an owning str sink is a copy
def string_as_str_sink(x: String) -> str:  # tpyc: mir(covered)
    t: str = x  # tpyc: mir_owned(t) mir_copy(t)
    return t


# free function: the bytes family follows the same rule
def bytes_slice(b: bytes) -> int:  # tpyc: mir(covered)
    v = b[1:]  # tpyc: mir_borrowed(v) mir_borrows(v, b)
    return len(v)


# free function: a stepped slice allocates and is refused for now
def stepped(s: str) -> int:  # tpyc: mir(uncovered /^stepped slice$/)
    return len(s[::2])


# free function: a Span parameter views its caller's container elements
def span_param(xs: Span[int32]) -> int32:  # tpyc: mir(covered)
    return xs[0]


# free function: a reassigned str parameter owns a local; the view borrows that
# local. Spelled StrView: an inferred local over a name the function rebinds
# owns under the one view rule, so only the explicit view reaches the copy.
def reassigned_param(s: str) -> int:  # tpyc: mir(covered)
    s = s + "x"
    v: StrView = s[1:]  # tpyc: mir_borrows(v, local(s))
    return len(v)


# free function: a loop join unions the origins
def loop_rebind(a: str, b: str, n: int32) -> int:  # tpyc: mir(covered)
    v = a
    i = 0
    while i < n:
        v = b
        i += 1
    return len(v)  # tpyc: mir_borrows(v, a|b)


# free function: the first str conflict -- the source is written while the view is live
def live_conflict(a: str) -> int:  # tpyc: mir(conflict /^replacement$/)
    t = a + "x"
    c: StrView = t  # tpyc: mir_borrowed(c) mir_borrows(c, t)
    t += "y"  # tpyc: warning(/while borrowed/) mir_write(t)
    return len(c)


# free function: sema's warning is not last-use aware (BUGS.md#borrow-warning-not-last-use-aware);
# MIR sees the view dead at the write
def last_use(a: str) -> int:  # tpyc: mir(covered)
    t = a + "x"
    c: StrView = t  # tpyc: mir_borrows(c, t)
    n = len(c)
    t += "y"  # tpyc: warning(/while borrowed/) mir_write(t)
    return n + len(t)


# free function: the bytes twin of the conflict (`+=` on bytes rebinds the source)
def live_conflict_bytes(a: bytes) -> int:  # tpyc: mir(conflict /^replacement$/)
    t = a + b"x"
    c: BytesView = t  # tpyc: mir_borrows(c, t)
    t += b"y"  # tpyc: warning(/while borrowed/) mir_write(t)
    return len(c)


# free function: a bytes view result borrows the parameter; the summary says which
def bytes_tail(b: bytes) -> BytesView:  # tpyc: mir(covered) mir_summary(known)
    return b[1:]


# free function: the bytes twins -- a literal is a static origin, a view result borrows the argument
def bytes_twins(b: bytes) -> int:  # tpyc: mir(covered)
    v = b"lit"  # tpyc: mir_borrows(v, static)
    w = bytes_tail(b)  # tpyc: mir_borrows(w, b)
    return len(v) + len(w)


G = "static global"


# free function: a view result rooted in a global lowers, but no summary names its origin
def returns_global_view() -> StrView:  # tpyc: mir(covered) mir_summary(opaque /^view result origin outside the parameters$/)
    return G


class Rec:
    # constructor: the str field is initialized by a copy of the view parameter
    def __init__(self, name: str, n: int32) -> None:  # tpyc: mir(covered)
        self.name = name  # tpyc: mir_copy(self.name)
        self.n = n

    # method: a str field read is a borrow of the field place
    def name_len(self) -> int:  # tpyc: mir(covered)
        return len(self.name)

    # method: a view of a field returned -- the origin is the field, param0.name
    def name_view(self) -> StrView:  # tpyc: mir(covered)
        return self.name

    # method: a field write is a replacement event on the field place
    def rename_m(self, s: str) -> None:  # tpyc: mir(covered)
        self.name = s  # tpyc: mir_write(self.name)


class Holder:
    # constructor: a view member stores the loan its view parameter holds
    def __init__(self, v: StrView) -> None:  # tpyc: mir(covered)
        self.v = v  # tpyc: mir_borrowed(self.v)


# free function: a field read through a borrowed record parameter
def read_len(r: Rec) -> int:  # tpyc: mir(covered)
    return len(r.name)


# free function: a field read at an owned sink copies
def read_copy(r: Rec) -> str:  # tpyc: mir(covered)
    return r.name


# free function: a field write is a replacement event on the field place
def write_field(r: Rec, s: str) -> None:  # tpyc: mir(covered)
    r.name = s  # tpyc: mir_write(r.name)


# free function: the in-place append reads the field through a holder, then replaces it
def append_field(r: Rec, s: str) -> None:  # tpyc: mir(covered)
    r.name += s  # tpyc: mir_write(r.name)


# free function: a str parameter may view the very field the write replaces (the caller
# can pass `r.name`), so reading `s` after the write is a possible conflict sema does not
# see (BUGS.md#param-view-of-replaced-field)
def write_then_reuse(r: Rec, s: str) -> int:  # tpyc: mir(conflict /^replacement$/)
    r.name = s  # tpyc: mir_write(r.name)
    return len(s)


# free function: a view local of a field borrows the field place. Spelled
# StrView: an inferred field read owns under the one view rule.
def field_view(r: Rec) -> int:  # tpyc: mir(covered)
    v: StrView = r.name  # tpyc: mir_borrowed(v) mir_borrows(v, r.name)
    return len(v)


# free function: the callee's field write is published and becomes a call-write event
def rename(r: Rec, s: str) -> None:  # tpyc: mir(covered) mir_summary(known)
    r.name = s


# free function: a forwarded record's field written by a callee
def forwarded_write(r: Rec, s: str) -> None:  # tpyc: mir(covered)
    rename(r, s)


# free function: sema owns the local before a call that may write its source
def field_view_then_write(r: Rec, s: str) -> int:  # tpyc: mir(covered)
    v = r.name  # tpyc: mir_owned(v) mir_copy(v)
    rename(r, s)
    return len(v)


# free function: the callee's published field write reaches the caller's str parameter,
# which may view that field, so reading it after the call is a possible conflict
def forwarded_then_reuse(r: Rec, s: str) -> int:  # tpyc: mir(conflict /^replacement$/)
    rename(r, s)
    return len(s)


# free function: a sibling scalar field write does not touch the str field's loan
def sibling(r: Rec) -> int:  # tpyc: mir(covered)
    return len(r.name) + r.n


# free function: a local record with a str field, written and read in place
def local_record(s: str) -> int:  # tpyc: mir(covered)
    r = Rec(s, 1)
    r.name = s  # tpyc: mir_write(r.name)
    return len(r.name)


# free function: a method returning a view of a field; the result's origin is the field
def method_view(r: Rec) -> int:  # tpyc: mir(covered)
    v = r.name_view()
    return len(v)


# free function: a str member copies out of a view argument
def ctor_from_view(s: str) -> int:  # tpyc: mir(covered)
    v = s[1:]  # tpyc: mir_borrows(v, s)
    r = Rec(v, 1)
    return len(r.name)


# free function: a str member copies out of a String argument
def ctor_from_string(x: String) -> int:  # tpyc: mir(covered)
    r = Rec(x, 1)
    return len(r.name)


# free function: the method's published field write becomes a call-write event
def method_write(r: Rec, s: str) -> None:  # tpyc: mir(covered)
    r.rename_m(s)  # tpyc: mir_write(r.name)


# free function: a literal's storage is never written, so a field write does not reach its view
def lit_then_write(q: Rec) -> int:  # tpyc: mir(covered)
    v = "lit"  # tpyc: mir_borrows(v, static)
    q.name = "x"  # tpyc: mir_write(q.name)
    return len(v)


def main() -> None:
    print("view_param:", view_param("abc"))
    print("slice_local:", slice_local("abcd"))
    print("slice_bounds:", slice_bounds("abcdef", 1, 4))
    print("slice_of_owned:", slice_of_owned("ab", "cd"))
    print("literal_view:", literal_view())
    print("view_return:", view_return("xyz"))
    print("view_return_caller:", view_return_caller("xyz"))
    print("owned_from_view:", owned_from_view("abc"))
    print("explicit_view_param:", explicit_view_param("pq"))
    print("string_as_str_param:", string_as_str_param(String("abcd")))
    print("string_as_str_sink:", string_as_str_sink(String("ab")))
    print("bytes_slice:", bytes_slice(b"abc"))
    print("stepped:", stepped("abcde"))
    print("span_param:", span_param([4, 5]))
    print("reassigned_param:", reassigned_param("ab"))
    print("loop_rebind:", loop_rebind("a", "bcd", 2))
    print("live_conflict:", live_conflict("a"))
    print("last_use:", last_use("a"))
    print("live_conflict_bytes:", live_conflict_bytes(b"a"))
    print("bytes_twins:", bytes_twins(b"abcd"))
    print("returns_global_view:", returns_global_view())
    r = Rec("name", 7)
    print("Rec:", r.name_len(), r.name_view())
    r.rename_m("renamed")
    print("rename_m:", r.name_len())
    h = Holder("held")
    print("Holder:", h.v)
    print("read_len:", read_len(r), "read_copy:", read_copy(r))
    write_field(r, "w")
    append_field(r, "+")
    print("write_field:", read_copy(r), "write_then_reuse:", write_then_reuse(r, "wr"))
    print("field_view:", field_view(r))
    forwarded_write(r, "fw")
    print("forwarded_write:", read_copy(r))
    print("field_view_then_write:", field_view_then_write(r, "later"), read_copy(r))
    print("sibling:", sibling(r))
    print("local_record:", local_record("lr"))
    print("forwarded_then_reuse:", forwarded_then_reuse(Rec("ft", 2), "ftr"))
    print("method_view:", method_view(Rec("mv", 3)))
    print("ctor_from_view:", ctor_from_view("abc"), "ctor_from_string:", ctor_from_string(String("xyz")))
    method_write(r, "mw")
    print("method_write:", read_copy(r))
    print("lit_then_write:", lit_then_write(r), read_copy(r))


main()
