# MIR's lifetime warnings across a loop's back edge, the holder reset or rebound in the loop: no
# warning where the reset comes before the buffer ends, "may" where some path reads only another
# storage the holder was rebound onto; the dangles MIR does not prove yet are marked missed.
from part_loops import Buf
from tpy import StrView, int32


class Tok:
    s: StrView
    n: int32

    def __init__(self, s: StrView, n: int32) -> None:
        self.s = s
        self.n = n


def mk(n: int32) -> str:
    return str(n) * 30


def clear_tok(t: Tok) -> None:
    t.s = "safe"


# no warning (correct): stored and read in the same pass
def same_iter(k: int32) -> None:
    for i in range(k):
        buf = mk(i)
        tk = Tok(buf, i)  # tpyc: ok
        print("same_iter", tk.s[0])


# no warning (correct): the holder is reset before the pass ends
def reset_in_body(k: int32) -> None:
    tk = Tok("x", 0)
    for i in range(k):
        buf = mk(i)
        tk = Tok(buf, i)  # tpyc: ok
        print("reset_in_body", tk.s[0])
        tk = Tok("y", 0)
    print("reset_in_body", tk.s[0])


# no warning (correct): the buffer is made before the loop
def outer_buf(k: int32) -> None:
    tk = Tok("x", 0)
    buf = mk(0)
    for i in range(k):
        tk = Tok(buf, i)  # tpyc: ok
    print("outer_buf", tk.s[0])


# no warning (correct): a conditional store reset through an alias of the record before the pass ends
def alias_reset(k: int32, t: Tok) -> None:
    alias = t
    for i in range(k):
        buf = mk(i)
        if i % 2 == 0:
            t.s = buf  # tpyc: ok
        alias.s = "safe"
    print("alias_reset", t.s[0])


# no warning (correct): the same reset before a break
def alias_reset_break(k: int32, t: Tok) -> None:
    alias = t
    for i in range(k):
        buf = mk(i)
        t.s = buf  # tpyc: ok
        alias.s = "safe"
        if i == 1:
            break
    print("alias_reset_break", t.s[0])


# known false warning (BUGS.md#mir-callee-definite-store-not-a-kill): the callee always resets t.s,
# but its published store only may run
def call_reset(k: int32, t: Tok) -> None:
    for i in range(k):
        buf = mk(i)
        if i % 2 == 0:
            t.s = buf
        clear_tok(t)  # tpyc: warning(/'buf' is freed at the end of this block, and 't.s' may still view it/)
    print("call_reset", t.s[0])


# the holder reset at the top of each pass, then left by continue or by the normal end: each frees the
# buffer the read after the loop views
def with_continue(k: int32) -> None:
    v: StrView = "safe"
    for i in range(k):
        v = "safe"
        buf = mk(i)
        v = buf
        if i % 2 == 0:  # tpyc: warning(/'buf' is freed at the end of this block, but 'v' still views it/)
            continue  # tpyc: warning(/'buf' is freed when control leaves its block here, but 'v' still views it/)
    print("with_continue", v[0])


# no warning (correct): the same, with the holder reset after the continue test and at the end of the pass
def with_continue_reset(k: int32) -> None:
    v: StrView = "safe"
    for i in range(k):
        buf = mk(i)
        v = buf  # tpyc: ok
        print("with_continue_reset", v[0])
        if i % 2 == 0:  # tpyc: ok
            v = "safe"
            continue  # tpyc: ok
        v = "safe"
    print("with_continue_reset", v[0])


# a view rebound each pass onto a parameter's field, then onto a pass-local buffer: the field's
# replacement through a name that may be either record warns "may"; the buffer's end warns nothing, since
# the next pass's replacement of the field it then views may land on the other record. A missed dangle with
# fewer than three passes (BUGS.md#mir-hazard-read-on-some-paths-silent): the read after the loop views
# the freed buffer, but the read in the loop views the live field
def overkill(b: Buf, c: Buf, k: int32, flag: bool) -> None:
    v: StrView = "safe"
    for i in range(k):
        v = b.s
        x = b if flag else c
        x.s = str(i) * 40  # tpyc: warning(/this assignment frees the old value of 'x.s', and 'v' may still view it and is read on line 126/)
        if i == 2:
            print("overkill", v[0])
            return
        buf = str(i) * 40
        v = buf  # tpyc: ok
    print("overkill", v[0])


# the same with the field replaced directly: each kill warns "may", since some path reads only the
# other storage's freed value (the buffer's after the loop, the field's in it)
def defkill(b: Buf, k: int32) -> None:
    v: StrView = "safe"
    for i in range(k):
        v = b.s
        b.s = str(i) * 40  # tpyc: warning(/this assignment frees the old value of 'b.s', and 'v' may still view it and is read on line 141/)
        if i == 2:
            print("defkill", v[0])
            return
        buf = str(i) * 40
        v = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'v' may still view it and is read on line 145/)
    print("defkill", v[0])


# a view reset, then rebound onto the field its kill replaced: the replacement through a name that may be
# either record is no kill of that field, so the read after it views the live field, and the first
# kill's read of the old field runs on some passes only. A missed dangle there
# (BUGS.md#mir-hazard-read-on-some-paths-silent); the second write warns "may"
def own_weak_reset(b: Buf, c: Buf, k: int32, flag: bool, read_old: bool) -> None:
    v: StrView = "safe"
    for i in range(k):
        v = b.s
        b.s = str(i) * 40  # tpyc: ok
        if read_old:
            print("own_weak_reset old", v[0])
        v = "safe"
        v = b.s
        x = b if flag else c
        x.s = str(i) * 40  # tpyc: warning(/this assignment frees the old value of 'x.s', and 'v' may still view it and is read on line 163/)
        print("own_weak_reset new", v[0])


# the same with the field replaced directly: it frees the field the view was renewed onto, so the
# first kill is read on every path (never run)
def own_direct_reset(b: Buf, k: int32, read_old: bool) -> None:
    v: StrView = "safe"
    for i in range(k):
        v = b.s
        b.s = str(i) * 40  # tpyc: warning(/this assignment frees the old value of 'b.s', but 'v' still views it and is read on line 174/)
        if read_old:
            print("own_direct_reset old", v[0])
        v = "safe"
        v = b.s
        x = b
        b.s = str(i) * 40  # tpyc: warning(/this assignment frees the old value of 'b.s', but 'v' still views it and is read on line 179/)
        print("own_direct_reset new", v[0])


# a field replaced before a loop whose only write to it goes through a name that may be either of two
# records: that write is no kill of the field, so the read after the loop is reached only by leaving a
# loop that crosses no kill, a "may" (never run)
def two_object_cycle(b: Buf, c: Buf, k: int32, flag: bool) -> None:
    v: StrView = b.s
    b.s = str(k) * 40  # tpyc: warning(/this assignment frees the old value of 'b.s', and 'v' may still view it and is read on line 193/)
    i = 0
    while i < k:
        x = b if flag else c
        x.s = str(i) * 40  # tpyc: warning(/this assignment frees the old value of 'x.s', and 'v' may still view it and is read on line 193/)
        i += 1
    print("two_object_cycle", v[0])


# known false warning (BUGS.md#mir-may-warning-correlated-conditions): the reset after the inner loop
# writes through a name that may stand for either of two records, so it does not end the view the inner
# loop kept, though every outer pass ends with it reset
def nested_reset(b: Buf, k: int32) -> None:
    t = Tok("safe", 0)
    for j in range(k):
        for i in range(k):
            t = Tok(b.s, i)
            b.s = str(i) * 40  # tpyc: warning(/this assignment frees the old value of 'b.s', and 't.s' may still view it/)
        t.s = "safe"
    print("nested_reset", t.s[0])


# no warning (correct): a copy of a member reset before it is copied
def safe_source_copy(k: int32, t: Tok) -> None:
    copied: StrView = "safe"
    for i in range(k):
        if i >= 0:
            a = mk(i)
            t.s = a  # tpyc: ok
        t.s = "safe"
        copied = t.s
        print("safe_source_copy", copied[0])
    print("safe_source_copy", copied[0])


# no warning (correct): an inner buffer replaced by the outer one, which is read while live, then reset
def nested_live_reset(k: int32, t: Tok) -> None:
    for i in range(k):
        outer = mk(i)
        if i % 2 == 0:
            inner = mk(i + 1)
            t.s = inner  # tpyc: ok
        t.s = outer
        print("nested_live_reset", t.s[0])
        t.s = "safe"
    print("nested_live_reset", t.s[0])


# missed dangle (BUGS.md#mir-hazard-read-on-some-paths-silent): the next pass reads the previous pass's
# buffer, but the last pass leaves without reading it
def next_iter(k: int32) -> None:
    prev = Tok("x", 0)
    for i in range(k):
        print("next_iter", prev.s[0])
        buf = mk(i)
        prev = Tok(buf, i)  # tpyc: ok


# missed dangle (BUGS.md#mir-hazard-read-on-some-paths-silent): the last pass may store the safe value,
# so not every path reads the buffer (run with an even count, whose last pass stores "safe")
def alternation(k: int32) -> None:
    v: StrView = "safe"
    for i in range(k):
        buf = mk(i)
        if i % 2 == 0:  # tpyc: ok
            v = buf  # tpyc: ok
        else:
            v = "safe"
    print("alternation", v[0])


def main() -> None:
    same_iter(2)
    reset_in_body(2)
    outer_buf(2)
    alias_reset(3, Tok("x", 0))
    alias_reset_break(3, Tok("x", 0))
    call_reset(3, Tok("x", 0))
    safe_source_copy(3, Tok("x", 0))
    nested_live_reset(3, Tok("x", 0))
    next_iter(1)
    with_continue(0)
    with_continue_reset(3)
    overkill(Buf("o" * 40), Buf("p" * 40), 3, False)
    defkill(Buf("o" * 40), 0)
    own_weak_reset(Buf("o" * 40), Buf("p" * 40), 3, False, False)
    nested_reset(Buf("o" * 40), 2)
    alternation(2)
