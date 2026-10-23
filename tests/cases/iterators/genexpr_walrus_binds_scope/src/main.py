# A walrus in a generator expression binds the variable of the enclosing
# function or module (PEP 572), at every position the expression can sit in;
# the frame writes the variable through its reference capture.
import asyncio
from typing import Iterable, Iterator, Optional
from tpy import int32, readonly, Span, SpanIter
from tplib.array_list import ArrayList


class Acc:
    # method
    def run(self, xs: list[int32]) -> int32:
        top = 0
        total = sum(x for x in xs if (top := x) > 1)  # tpyc: ok
        return total * 100 + top


# generator body
def gen_body(xs: list[int32]) -> Iterator[int32]:
    seen = 0
    yield sum(x for x in xs if (seen := x) > 0)  # tpyc: ok
    yield seen


# async body
async def async_body(xs: list[int32]) -> int32:
    seen = 0
    await asyncio.sleep(0)
    s = sum(x for x in xs if (seen := x) > 0)  # tpyc: ok
    return s * 100 + seen


# function
def function() -> None:
    last = 0
    total = sum(x for x in range(6) if (last := x) > 2)  # tpyc: ok
    print("function", total, last)


# nested genexpr: the inner frame writes through the outer one
def nested(n: int32) -> None:
    deep = 0
    t = sum(sum((deep := i * 10 + j) for j in range(2)) for i in range(n))  # tpyc: ok
    print("nested", t, deep)


# list comprehension in a genexpr: its walrus binds through the frame
def listcomp_in_genexpr() -> None:
    last = 0
    t = sum(len([(last := i * j) for j in range(3)]) for i in range(3))  # tpyc: ok
    print("listcomp", t, last)


g_count = 0


# global: a store from another body narrows into the inferred int32
def uses_global(xs: list[int]) -> None:
    global g_count
    t = sum(1 for i in xs if (g_count := i) >= 0)  # tpyc: warning(/'g_count' was inferred as int32 in its module; this store from a generator expression narrows int into it/)
    print("global", t, g_count)


# nonlocal: the enclosing scope is a nested def, writing its own nonlocals
def nonlocal_def() -> None:
    k = 0
    s = "a"

    def inner() -> None:
        nonlocal k, s
        t = sum(x for x in range(5) if (k := x) > 2)  # tpyc: ok
        u = "".join(c for c in ["x", "y", "z"] if (s := c) != "y")  # tpyc: ok
        print("nonlocal-in", t, k, u, s)

    inner()
    print("nonlocal", k, s)


def relay(it: Iterable[int32]) -> Iterator[int32]:
    for v in it:
        yield v


# kept: each pull of a kept generator expression writes the variable
def kept(xs: list[int32]) -> None:
    last = 0
    g = relay(x for x in xs if (last := x) > 0)
    a = next(g, 0)
    print("kept", a, last)
    b = next(g, 0)
    print("kept", b, last)


# lazy: `any` stops at the first match, and so do the stores
def lazy(xs: list[int32]) -> None:
    hit = 0
    found = any((hit := x) > 1 for x in xs)  # tpyc: ok
    print("lazy", found, hit)


# match arm
def match_arm(v: int32) -> None:
    last = 0
    match v:
        case 1:
            t = sum(x for x in range(4) if (last := x) > 0)  # tpyc: ok
            print("match", t, last)
        case _:
            pass


# try / finally
def try_finally() -> None:
    last = 0
    try:
        t = sum(x for x in range(4) if (last := x) > 0)  # tpyc: ok
    finally:
        print("finally", last)
    print("try", t, last)


class Ctx:
    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# with body
def with_body() -> None:
    last = 0
    with Ctx():
        t = sum(x for x in range(4) if (last := x) > 0)  # tpyc: ok
    print("with", t, last)


# param: the target is a parameter of the enclosing function
def param_target(last: int32) -> None:
    t = sum(x for x in range(4) if (last := x) > 0)  # tpyc: ok
    print("param", t, last)


# optional: the store writes the whole Optional, and the read after it is
# checked again
def optional_target() -> None:
    o: int32 | None = 5
    n = sum(1 for i in range(3) if (o := None) is None)  # tpyc: ok
    if o is None:
        print("optional", n, "none")
    else:
        print("optional", n, o + 1)


# str / bytes: a store of a loop variable's text makes the enclosing
# variable own it
def str_target(words: list[str], blobs: list[bytes]) -> None:
    s = "start"  # tpyc: type(str)
    n = sum(1 for w in words if len(s := w) > 0)  # tpyc: ok
    print("str", n, s)
    b = b"x"
    k = sum(1 for v in blobs if len(b := v) > 0)  # tpyc: ok
    print("bytes", k, b)


o_glob: Optional[int32] = 5


def reset() -> int32:
    global o_glob
    o_glob = 7
    return 1


# pull: a pull of a generator runs TPy code, which may rebind a global, so
# the narrowing does not survive it
def pull_kills() -> None:
    g = relay(reset() for i in range(2))
    total = 0
    if o_glob is not None:
        for v in g:
            total += o_glob + v  # tpyc: warning(/Potential None access/)
    print("pull", total)


def produce() -> Iterator[int32]:
    global o_glob
    o_glob = 3
    yield 1


# in: a membership test pulls the generator
def in_pull() -> None:
    g = produce()
    r = 0
    if o_glob is not None and 1 in g:
        r = o_glob + 1  # tpyc: warning(/Potential None access/)
    print("in", r)


# resume: a capture's narrowing does not survive the frame's suspension,
# where the loop it feeds rebinds the variable
def capture_resume() -> None:
    o: Optional[int32] = 5
    for v in (o + j for i in range(1) if o is not None for j in range(2)):  # tpyc: warning(/Potential None access/)
        print("resume", v)
        o = 9


# condition: the frame the `any` pulls writes `o`, so the narrowing the
# left operand proved does not reach the body
def cond_if() -> None:
    o: Optional[int32] = 5
    if o is not None and any((o := 7) is not None for _ in range(1)):
        print("cond-if", o + 1)  # tpyc: warning(/Potential None access/)


# and-chain: the same, inside one expression
def cond_and() -> None:
    o: Optional[int32] = 5
    r = o is not None and any((o := 7) is not None for _ in range(1)) and o + 1 > 0  # tpyc: warning(/Potential None access/)
    print("cond-and", r)


# comprehension pull: a list comprehension in a condition pulls a kept
# generator, whose frame writes `value`
def comp_pull() -> None:
    value: Optional[int32] = 5
    g = relay(i for i in range(1) if (value := 8) is not None)
    if value is not None and len([x for x in g]) > 0:
        print("comp-pull", value + 1)  # tpyc: warning(/Potential None access/)


class Bag:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    @readonly
    def __span__(self) -> Span[readonly[int32]]:
        return self.xs

    @readonly
    def __iter__(self) -> SpanIter[readonly[int32]]:
        global o_glob
        o_glob = 4
        return SpanIter(self.__span__())


# record iter: a record whose `__iter__` is TPy code runs it at the loop's
# start, though it hands out a view of its storage
def record_iter() -> None:
    b = Bag()
    total = 0
    if o_glob is not None:
        for v in b:
            total += o_glob + v  # tpyc: warning(/Potential None access/)
    print("record-iter", total)


# genexpr in a list comprehension: the walrus binds the function's variable
# through both
def genexpr_in_listcomp() -> None:
    last = 0
    r = [sum((last := i * 10 + j) for j in range(2)) for i in range(2)]  # tpyc: ok
    print("genexpr-in-listcomp", r, last)


# nested def: the walrus writes the nested def's own local
def nested_def_own() -> None:
    def inner() -> None:
        own = 0
        t = sum(x for x in range(3) if (own := x) >= 0)  # tpyc: ok
        print("nested-def-own", t, own)

    inner()


# zip: a list's steps run no TPy code and keep the narrowing; a zip over a
# generator pulls it
def zip_pull(xs: list[int32]) -> None:
    pulled = zip(produce(), xs)
    total = 0
    if o_glob is not None:
        for v in xs:
            total += o_glob + v  # tpyc: ok
        for q in pulled:
            total += o_glob + q[0]  # tpyc: warning(/Potential None access/)
    print("zip", total)


# ternary: an arm's frame writes `o`, so the narrowing does not survive the
# ternary whichever arm ran
def ternary_genexpr(flag: bool) -> None:
    o: Optional[int32] = 5
    if o is not None:
        r = any((o := 6) is not None for _ in range(1)) if flag else False
        print("ternary-genexpr", r, o + 1)  # tpyc: warning(/Potential None access/)


# stdlib container: ArrayList's `__iter__` and `__contains__` are TPy code
def array_list_pull() -> None:
    al = ArrayList[int32, 4]()
    al.append(1)
    total = 0
    if o_glob is not None:
        for v in al:
            total += o_glob + v  # tpyc: warning(/Potential None access/)
    if o_glob is not None and 1 in al:
        # Spelled out: `total += o_glob` is refused (BUGS.md#aug-assign-killed-optional-refused).
        total = total + o_glob  # tpyc: warning(/Potential None access/)
    print("array-list", total)


# module level, a nested pair of frames included
m_last = 0
m_total = sum(x for x in range(5) if (m_last := x) > 1)  # tpyc: ok
print("module", m_total, m_last)
q = 0
qt = sum(sum((q := i * 10 + j) for j in range(2)) for i in range(2))  # tpyc: ok
print("module-nested", qt, q)
m_word = "a"
m_n = sum(1 for w in ["ab", "cd"] if len(m_word := w) > 0)  # tpyc: ok
print("module-str", m_n, m_word)
# module level: a variable both branches of an `if` bind
if m_n > 0:
    m_branch = 0
else:
    m_branch = 1
m_found = any((m_branch := x) > 1 for x in [1, 2])  # tpyc: ok
print("module-branch", m_found, m_branch)

function()
print("method", Acc().run([0, 2, 5]))
print("generator", list(gen_body([3, 4, 0])))
print("async", asyncio.run(async_body([3, 4, 0])))
nested(2)
listcomp_in_genexpr()
uses_global([3, 1])
nonlocal_def()
kept([4, 6])
lazy([0, 2, 5])
match_arm(1)
try_finally()
with_body()
param_target(9)
optional_target()
str_target(["ab", "cd"], [b"q", b"rs"])
pull_kills()
in_pull()
capture_resume()
cond_if()
cond_and()
comp_pull()
record_iter()
genexpr_in_listcomp()
nested_def_own()
zip_pull([1, 2])
ternary_genexpr(True)
array_list_pull()
