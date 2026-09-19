# The TRANSIENT reads of a @property getter off a TEMPORARY receiver -- the
# half of the dying-source rule that must keep COMPILING. Each section finishes
# inside its own full expression, so the `mk()` temporary is still alive where
# the borrow is read; the BINDING positions, which are not, are pinned by the
# records/error_property_off_temporary_receiver* family (its `_arg` case holds
# the tag table).
# The argument sections are the ones with a second question: an argument is
# admitted only when the CALLEE provably reads it during the call, so `len`,
# `str()` and a user function taking a view are here while `reversed`,
# `enumerate`, `zip`, `map` and `filter` are rejects (their `Iterator[T]`
# return is a lazy view of the argument, pinned by
# error_property_off_temporary_receiver_retaining_builtin).
# Nothing binds here, so there is no copy-vs-alias boundary to force: what the
# `live` section forces instead is that the read reaches the LIVE storage --
# it mutates the global the getter lends BEFORE reading, and both languages
# must show the new element.
from tpy import Own, StrView, int32

G: list[int32] = [1, 2]


class Rec:
    x: int32

    def __init__(self) -> None:
        self.x = 7


GREC: Rec = Rec()


class H:
    s: str

    def __init__(self) -> None:
        self.s = "abcdef"

    @property
    def data(self) -> list[int32]:
        return G

    @property
    def head(self) -> StrView:
        return self.s[0:3]

    @property
    def rec(self) -> Rec:
        return GREC


def mk() -> Own[H]:
    return H()


def takes_str(s: str) -> int32:
    return len(s)


class Sink:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def count_chars(self, v: StrView) -> int32:
        return len(v)


class Holder:
    v: str

    def __init__(self, v: StrView) -> None:
        self.v = str(v)


def main() -> None:
    # print argument: the container streams through its printer wrap
    print("print:", mk().data)
    # a native builtin whose parameter is read INSIDE the call
    print("len:", len(mk().data))
    print("len_view:", len(mk().head))
    # subscript receiver
    print("subscript:", mk().data[0])
    # f-string interpolation
    print("fstring:", f"{mk().head}")
    # comparison operand
    print("compare:", mk().head == "abc")
    # binary operand
    print("concat:", mk().head + "z")
    # method receiver off the getter result
    print("method:", mk().data.count(1))
    # a user function's `str` parameter, which takes a view for the call
    print("user_param:", takes_str(mk().head))
    # the str() conversion, which copies the view into owned storage
    print("str_call:", str(mk().head))
    # truthiness operand
    if mk().head:
        print("truthy: yes")
    # field read through a RECORD getter's result
    print("field_thru:", mk().rec.x)
    # augmented-assignment source: the extend reads it inside the statement
    acc: list[int32] = [0]
    acc += mk().data
    print("augassign:", len(acc))
    text = "z"
    text += mk().head
    print("augassign_str:", text)
    # a dict KEY, read by the lookup and copied by the write
    d: dict[str, int32] = {"abc": 1}
    print("dict_read:", d[mk().head])
    d[mk().head] = 2
    print("dict_write:", d["abc"])
    # the membership needle
    print("membership:", mk().head in d)
    # a user-record METHOD argument: the callee's own body says it only reads
    sink = Sink()
    print("method_arg:", sink.count_chars(mk().head))
    # a user-record CONSTRUCTOR argument, which copies the view into the field
    holder = Holder(mk().head)
    print("ctor_arg:", holder.v)
    # the read reaches LIVE storage, not a snapshot taken earlier
    G.append(3)
    print("live:", len(mk().data), mk().data[2])


main()
