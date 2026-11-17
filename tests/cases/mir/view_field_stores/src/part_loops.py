# MIR's store escapes from a loop (rule 3): a store renewed each pass reaches the return with the
# last pass's buffer; a reset after the loop, or a store on no path to the return, gives no warning; a
# call storing the same loan again keeps the escape, one storing another loan ends it.
from tpy import StrView, int32


class Tok:
    s: StrView

    def __init__(self, s: StrView) -> None:
        self.s = s


def mk(n: int32) -> str:
    return str(n) * 30


# a store on one pass only: no later pass rewrites it, so it escapes
def cond_store(t: Tok, k: int32) -> None:
    for i in range(k):
        buf = mk(i)
        if i == 2:
            t.s = buf  # tpyc: warning(/'buf' is freed when 'cond_store' returns, but 't.s' keeps a view of it/)


# a loop that renews no store follows the store: the return is reached only if it ends, so a "may"
def store_then_spin(t: Tok, k: int32) -> None:
    buf = mk(k)
    t.s = buf  # tpyc: warning(/'buf' is freed when 'store_then_spin' returns, and 't.s' may keep a view of it/)
    i = 0
    while i < k:
        i += 1


# no warning (correct): the record is reset after the loop
def store_reset(t: Tok, k: int32) -> None:
    for i in range(k):
        buf = mk(i)
        t.s = buf  # tpyc: ok
        print("store_reset", t.s[0])
    t.s = "safe"


# no warning (correct): each pass resets the record before its buffer ends
def store_reset_in_body(t: Tok, k: int32) -> None:
    for i in range(k):
        buf = mk(i)
        t.s = buf  # tpyc: ok
        print("store_reset_in_body", t.s[0])
        t.s = "safe"


def set_same(t: Tok, s: StrView) -> None:
    t.s = s


# a call storing the same loan again rewrites the member but keeps the escape (never run)
def fill_same(t: Tok, k: int32) -> None:
    buf = mk(k)
    t.s = buf  # tpyc: warning(/'buf' is freed when 'fill_same' returns, but 't.s' keeps a view of it/)
    set_same(t, buf)


# the same across a loop's back edge (never run)
def fill_same_loop(t: Tok, k: int32) -> None:
    for i in range(k):
        buf = mk(i)
        t.s = buf  # tpyc: warning(/'buf' is freed when 'fill_same_loop' returns, but 't.s' keeps a view of it/)
        set_same(t, buf)


# no warning (correct): a call storing a different loan ends the store
def fill_other(t: Tok, k: int32) -> None:
    buf = mk(k)
    t.s = buf  # tpyc: ok
    set_same(t, "safe")


# no warning (correct): the same across a loop's back edge
def fill_other_loop(t: Tok, k: int32) -> None:
    for i in range(k):
        buf = mk(i)
        t.s = buf  # tpyc: ok
        set_same(t, "safe")


def main() -> None:
    t = Tok("x")
    cond_store(t, 2)
    print("cond_store", t.s)
    store_reset(t, 2)
    print("store_reset", t.s)
    store_reset_in_body(t, 2)
    print("store_reset_in_body", t.s)
    fill_other(t, 2)
    print("fill_other", t.s)
    fill_other_loop(t, 2)
    print("fill_other_loop", t.s)
