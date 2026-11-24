# A finally-deferred `return s`: the object `s` holds is captured before the
# finally chain and converted into the result after it, at the member sema
# landed it in, so a finally mutation shows in the returned object and a
# subclass object lands as its base, as the eager return converts it --
# whether `s` is a local, a pointer-bound one, a narrowed Optional (a
# local's, a loop variable's), an owned parameter or an owned Optional
# parameter (narrowed, its payload; read whole, the whole Optional), at a
# union, an Optional or a plain result (an async union return is refused by
# the frame's union payload gate).
from typing import Iterator
from tpy import Own, int32


class FBase:
    n: int32

    # Takes the subclass by value so the union's converting constructor
    # would pick this, not the slicing upcast CPython's alias is.
    def __init__(self, src: "FSub") -> None:
        self.n = src.n + 1000


class FSub(FBase):
    # Skips FBase.__init__, which would build from a subclass object.
    def __init__(self, n: int32) -> None:  # tpyc: warning(/does not call 'super/)
        self.n = n


def show(tag: str, r: FBase | int32) -> None:
    if isinstance(r, FBase):
        print(tag, r.n)


# subclass: the FSub lands as FBase after the finally ran
def subclass() -> Own[FBase | int32]:
    s = FSub(5)
    try:
        return s  # tpyc: warning(/narrows 'FSub'/)
    finally:
        s.n += 10


# same type: the FBase is moved out as it is
def same_type() -> Own[FBase | int32]:
    s = FBase(FSub(1))
    try:
        return s  # tpyc: ok
    finally:
        s.n += 10


# nested finally: both chains run before the conversion
def nested() -> Own[FBase | int32]:
    s = FSub(5)
    try:
        try:
            return s  # tpyc: warning(/narrows 'FSub'/)
        finally:
            s.n += 1
    finally:
        s.n += 100


# str member: an owned str is moved out after the finally read it
def str_member(s: Own[str]) -> str | int32:
    try:
        return s  # tpyc: ok
    finally:
        print("finally-str-fin", s)


# the finally rebinds `s`: the return keeps the object it read
def rebinding() -> Own[FBase | int32]:
    s = FSub(5)
    try:
        return s  # tpyc: warning(/narrows 'FSub'/)
    finally:
        s = FSub(7)


# a pointer-bound local (rebound before the try): the read is the object
# it points at, a subclass one and one of the member's own type
def pointer_bound(flag: bool) -> Own[FBase | int32]:
    s = FSub(1)
    if flag:
        s = FSub(2)
    try:
        return s  # tpyc: warning(/narrows 'FSub'/)
    finally:
        s.n += 40


def pointer_bound_same(flag: bool) -> Own[FBase | int32]:
    s = FBase(FSub(1))
    if flag:
        s = FBase(FSub(2))
    try:
        return s  # tpyc: ok
    finally:
        s.n += 40


# a narrowed Optional local: the read is its payload
def narrowed_optional() -> Own[FBase | int32]:
    s: FSub | None = FSub(5)
    if s is not None:
        try:
            return s  # tpyc: warning(/narrows 'FSub'/)
        finally:
            s.n += 10
    return 0


# an owned parameter: its record lands as the base, not through FBase's
# constructor taking the subclass
def owned_param(s: Own[FSub]) -> Own[FBase | int32]:
    try:
        return s  # tpyc: warning(/narrows 'FSub'/)
    finally:
        s.n += 10


def gen_opt() -> Iterator[Own[FSub | None]]:
    yield FSub(5)


# a loop variable over owned Optionals, narrowed: the read is its payload,
# at a union and at an Optional result
def loop_optional() -> Own[FBase | int32]:
    for s in gen_opt():
        if s is not None:
            try:
                return s  # tpyc: warning(/narrows 'FSub'/)
            finally:
                s.n += 10
    return 0


def loop_optional_result() -> Own[FBase | None]:
    for s in gen_opt():
        if s is not None:
            try:
                return s  # tpyc: warning(/narrows 'FSub'/)
            finally:
                s.n += 20
    return None


# at an Optional of the payload's own type: a narrowed loop variable and
# an owned Optional parameter capture the payload, not the Optional
def loop_optional_same() -> Own[FSub] | None:
    for s in gen_opt():
        if s is not None:
            try:
                return s  # tpyc: ok
            finally:
                s.n += 30
    return None


# (the "never consumed" warning is BUGS.md#own-optional-param-narrowed-return-uncredited)
def param_optional_same(s: Own[FSub | None]) -> Own[FSub] | None:  # tpyc: warning(/never consumed/)
    if s is not None:
        try:
            return s  # tpyc: ok
        finally:
            s.n += 30
    return None


# un-narrowed, an owned Optional parameter is captured and moved whole: an
# empty one stays empty, a held one shows the finally's write
def param_optional_whole(s: Own[FSub | None]) -> Own[FSub] | None:
    try:
        return s  # tpyc: ok
    finally:
        if s is not None:
            s.n += 30


# un-narrowed, the loop variable's Optional is returned whole: copied
# before a finally that only reads it (a mutating one is
# BUGS.md#whole-storage-optional-finally-return-copies; at the base's
# Optional, BUGS.md#subclass-optional-at-base-optional-refused)
def loop_optional_whole() -> Own[FSub | None]:
    for s in gen_opt():
        try:
            return s  # tpyc: warning(/copies FSub \| None into owned storage/)
        finally:
            if s is not None:
                print("finally-loop-optional-whole-fin", s.n)
    return None


# a non-union result converts the same way: an Optional and the scalar
def optional_result() -> Own[FBase | None]:
    s = FSub(5)
    try:
        return s  # tpyc: warning(/narrows 'FSub'/)
    finally:
        s.n += 10


def scalar_result() -> Own[FBase]:
    s = FSub(5)
    try:
        return s  # tpyc: warning(/narrows 'FSub'/)
    finally:
        s.n += 10


def main() -> None:
    show("finally-pointer", pointer_bound(True))
    show("finally-pointer-same", pointer_bound_same(True))
    show("finally-narrowed-optional", narrowed_optional())
    show("finally-owned-param", owned_param(FSub(5)))
    show("finally-loop-optional", loop_optional())
    lo = loop_optional_result()
    if lo is not None:
        print("finally-loop-optional-result", lo.n)
    ls = loop_optional_same()
    if ls is not None:
        print("finally-loop-optional-same", ls.n)
    ps = param_optional_same(FSub(6))
    if ps is not None:
        print("finally-param-optional-same", ps.n)
    print("finally-param-optional-whole-none",
          param_optional_whole(None) is None)
    pw = param_optional_whole(FSub(7))
    if pw is not None:
        print("finally-param-optional-whole", pw.n)
    lw = loop_optional_whole()
    if lw is not None:
        print("finally-loop-optional-whole", lw.n)
    o = optional_result()
    if o is not None:
        print("finally-optional", o.n)
    print("finally-scalar", scalar_result().n)
    show("finally-subclass", subclass())
    show("finally-same", same_type())
    show("finally-nested", nested())
    t = str_member("ab")
    print("finally-str", t)
    show("finally-rebind", rebinding())

