# Send/Sync trait derivation for built-in type forms (marker layer only,
# no enforcement sites). Asserts the compiler's answers via
# tpyc: is_send/is_sync annotations on declarations.
from tpy import int32
from tplib.rc import Rc

def main() -> None:
    n = 42                      # tpyc: is_send(yes) is_sync(yes)
    f = 1.5                     # tpyc: is_send(yes) is_sync(yes)
    ok = True                   # tpyc: is_send(yes) is_sync(yes)
    s = "hello"                 # tpyc: is_send(no) is_sync(yes)
    # Same literal, two types, opposite Sync: an explicit list is mutable
    # (Sync no); an unmutated literal deduces to the value-type Array (Sync
    # yes). Mutating `ar` would deduce list and flip it to is_sync(no).
    xs: list[int32] = [1, 2, 3]  # tpyc: type(list[int32]) is_send(yes) is_sync(no)
    ar = [1, 2, 3]              # tpyc: type(Array[int32, 3]) is_send(yes) is_sync(yes)
    st = {1, 2}                 # tpyc: is_send(yes) is_sync(no)
    d = {1: 2}                  # tpyc: is_send(yes) is_sync(no)
    t = (1, 2.5)                # tpyc: is_send(yes) is_sync(yes)
    bv = b"abc"                 # tpyc: is_send(no) is_sync(yes)
    ba = bytearray(b"abc")      # tpyc: is_send(yes) is_sync(no)
    rc = Rc.new(7)              # tpyc: is_send(no) is_sync(no)
    print(n, f, ok, s)
    print(len(xs), len(ar), len(st), len(d), t[0], len(bv), len(ba), rc.get())

main()
