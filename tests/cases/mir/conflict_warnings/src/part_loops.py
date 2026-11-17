# MIR's lifetime warnings across a loop's back edge: a holder rebound each pass onto that pass's
# buffer still views the last one after the loop; a holder reset before the pass ends gives no warning.
from tpy import Span, StrView, int32


class Tok:
    s: StrView
    n: int32

    def __init__(self, s: StrView, n: int32) -> None:
        self.s = s
        self.n = n


def mk(n: int32) -> str:
    return str(n) * 30


# store in the body, read after a for loop: the last pass's buffer is freed
def after_for(k: int32) -> None:
    tk = Tok("x", 0)
    for i in range(k):
        buf = mk(i)
        tk = Tok(buf, i)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it/)
    print("after_for", tk.s[0])


# the same after a while loop
def after_while(k: int32) -> None:
    tk = Tok("x", 0)
    i = 0
    while i < k:
        buf = mk(i)
        tk = Tok(buf, i)
        i += 1  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it/)
    print("after_while", tk.s[0])


# an inner loop's buffer read in the outer loop's body
def inner_loop(k: int32) -> None:
    for j in range(k):
        tk = Tok("x", 0)
        for i in range(k):
            buf = mk(i + j)
            tk = Tok(buf, i)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it/)
        print("inner_loop", tk.s[0])


# a local view: the hint makes the view owned, since a variable of the body is made again each pass
def view_local(k: int32) -> None:
    v: StrView = "x"
    for i in range(k):
        buf = mk(i)
        v = buf  # tpyc: warning(/'buf' is freed at the end of this block, but 'v' still views it and is read on line 55; create 'buf' before the block, or make 'v' an owned 'str'$/)
    print("view_local", v[0])


# a conditional store in the body
def cond_store(k: int32) -> None:
    tk = Tok("x", 0)
    for i in range(k):
        buf = mk(i)
        if i == 2:  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it/)
            tk = Tok(buf, i)
    print("cond_store", tk.s[0])


# a field store into a record made before the loop
def field_store(k: int32) -> None:
    tk = Tok("x", 0)
    for i in range(k):
        buf = mk(i)
        tk.s = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it/)
    print("field_store", tk.s[0])


# a view of the loop variable, a copy of the element made each pass: no "create it before the block"
def loop_var(ss: list[str]) -> None:
    tk = Tok("x", 0)
    for s in ss:
        tk = Tok(s, 0)  # tpyc: warning(/'s' is freed at the end of this block, and 'tk.s' may still view it and is read on line 82; make 'Tok.s' an owned 'str'$/)
    print("loop_var", tk.s[0])


# a temporary in the loop: binding it to a variable of the body does not help
def temp_in_loop(k: int32) -> None:
    tk = Tok("x", 0)
    for i in range(k):
        tk = Tok(mk(i), i)  # tpyc: warning(/the temporary 'mk\(i\)' lives only until the end of this statement, and 'tk.s' may still view it and is read on line 90; make 'Tok.s' an owned 'str'$/)
    print("temp_in_loop", tk.s[0])


# break and the normal end both free the buffer the read after the loop may view
def store_break(k: int32) -> None:
    tk = Tok("x", 0)
    for i in range(k):
        buf = mk(i)
        tk = Tok(buf, i)
        if i == 1:  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it/)
            break  # tpyc: warning(/'buf' is freed when control leaves its block here, and 'tk.s' may still view it/)
    print("store_break", tk.s[0])


# continue and the normal end both free the buffer
def second_exit(k: int32) -> None:
    v: StrView = "safe"
    for i in range(k):
        buf = mk(i)
        v = buf
        if i % 2 == 0:  # tpyc: warning(/'buf' is freed at the end of this block, but 'v' still views it/)
            continue  # tpyc: warning(/'buf' is freed when control leaves its block here, but 'v' still views it/)
    print("second_exit", v[0])


# a holder rebound straight from one arm's buffer to the other's: each arm's end warns "may", since the
# read after the loop views the other arm's buffer on some paths
def both_arms(k: int32, choose: bool) -> None:
    v: StrView = "safe"
    for i in range(k):
        if choose:
            a = mk(i)
            v = a  # tpyc: warning(/'a' is freed at the end of this block, and 'v' may still view it/)
        else:
            b = mk(i)
            v = b  # tpyc: warning(/'b' is freed at the end of this block, and 'v' may still view it/)
    print("both_arms", v[0])


# the next pass reads the previous pass's buffer before rebinding the holder
def read_old_after_reentry(k: int32) -> None:
    v: StrView = "safe"
    for i in range(k):
        buf = mk(i)
        print("read_old_after_reentry", v[0])
        v = buf  # tpyc: warning(/'buf' is freed at the end of this block, but 'v' still views it/)
    print("read_old_after_reentry", v[0])


# a copy reset and reused for another buffer: only that buffer warns, never the first. Sema's warning at
# `v = "safe"` is false: BUGS.md#view-copy-source-rebind-false-warning
def family_reused(k: int32) -> None:
    v: StrView = "x"
    copied: StrView = "x"
    for i in range(k):
        if i >= 0:
            a = mk(i)
            v = a  # tpyc: ok
        copied = v
        v = "safe"  # tpyc: warning(/Mutation of 'v' while borrowed/)
        copied = "safe"
        b = mk(i + 1)
        copied = b  # tpyc: warning(/'b' is freed at the end of this block, but 'copied' still views it/)
    print("family_reused", copied[0])


# the same with the copy taken in the arm, before the reset: only b warns, never a (the same false sema
# warning at `v = "safe"`: BUGS.md#view-copy-source-rebind-false-warning)
def family_reused_after_end(k: int32) -> None:
    v: StrView = "safe"
    copied: StrView = "safe"
    for i in range(k):
        if i >= 0:
            a = mk(i)
            v = a  # tpyc: ok
            copied = v  # tpyc: ok
        v = "safe"  # tpyc: warning(/Mutation of 'v' while borrowed/)
        copied = "safe"
        b = mk(i + 1)
        copied = b  # tpyc: warning(/'b' is freed at the end of this block, but 'copied' still views it/)
    print("family_reused_after_end", copied[0])


class Buf:
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


# a view of a parameter's field, rebound each pass before the field is replaced: the replacement
# frees what the view of the last pass keeps
def external_loop(b: Buf, k: int32) -> None:
    t = Tok("safe", 0)
    for i in range(k):
        t = Tok(b.s, i)
        b.s = str(i) * 40  # tpyc: warning(/this assignment frees the old value of 'b.s', and 't.s' may still view it and is read on line 187/)
    print("external_loop", t.s[0])


# the same without a loop: definite
def external_once(b: Buf, flag: bool) -> None:
    t = Tok(b.s, 0)
    if flag:
        b.s = "x" * 40  # tpyc: warning(/this assignment frees the old value of 'b.s', but 't.s' still views it and is read on line 195/)
    print("external_once", t.s[0])


# the same in a loop over a local record's field
def local_field_loop(k: int32) -> None:
    b = Buf("original" * 40)
    t = Tok("safe", 0)
    for i in range(k):
        t = Tok(b.s, i)
        b.s = str(i) * 40  # tpyc: warning(/this assignment frees the old value of 'b.s', and 't.s' may still view it and is read on line 205/)
    print("local_field_loop", t.s[0])


# no warning (correct): the view is reset before the pass ends
def external_loop_reset(b: Buf, k: int32) -> None:
    t = Tok("safe", 0)
    for i in range(k):
        t = Tok(b.s, i)
        b.s = str(i) * 40  # tpyc: ok
        t = Tok("safe", 0)
    print("external_loop_reset", t.s[0])


# an inner loop grows the list the outer loop walks
def cursor_inner(a: list[int32]) -> None:
    first = True
    for x in a:
        if first:
            first = False
            for i in range(4):
                a.append(9)  # tpyc: warning(/this call may move the elements of 'a', but the iteration over 'a' still walks them/)
        print("cursor_inner", x)


# the joined-source face of the same
def cursor_joined(a: list[int32], b: list[int32], flag: bool) -> None:
    v = a if flag else b
    first = True
    for x in v:
        if first:
            first = False
            for i in range(4):
                a.append(9)  # tpyc: warning(/this call may move the elements of 'a', and the iteration over 'v' may still walk them/)
        print("cursor_joined", x)


# a Span over a list grown in a loop, read after it
def span_loop(k: int32) -> int32:
    a = [1, 2]
    s: Span[int32] = a[0:]
    for i in range(k):
        a.append(i)  # tpyc: warning(/this call may move the elements of 'a', but 's' still views them/)
    return s[0]


# a block end then a loop that frees nothing: the read is reached only if the loop ends, so a "may"
def spin_then_read(k: int32, f: bool) -> None:
    v: StrView = "safe"
    if f:
        buf = mk(1)
        v = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'v' may still view it/)
    i = 0
    while i < k:
        i += 1
    print("spin_then_read", v[0])


def main() -> None:
    after_for(0)
    after_while(0)
    inner_loop(0)
    view_local(0)
    cond_store(2)
    field_store(0)
    loop_var([])
    temp_in_loop(0)
    store_break(0)
    second_exit(0)
    both_arms(0, True)
    read_old_after_reentry(0)
    family_reused(0)
    family_reused_after_end(0)
    external_loop(Buf("o" * 40), 0)
    external_once(Buf("o" * 40), False)
    local_field_loop(0)
    external_loop_reset(Buf("o" * 40), 2)
    cursor_inner([])
    cursor_joined([1], [2], False)
    print("span_loop", span_loop(0))
    spin_then_read(0, False)
