# The one declaration stands before an unrelated sibling loop between the
# binding loop and the read, per local kind and per position.
#
# `sib_readonly` is the one parity-blind section here: a readonly binding
# forbids the mutation that would make a silent copy visible, so it only
# pins the const-pointer render.
from typing import Optional
from tpy import int32, error_return, readonly
from tplib import Box
from fixtures import Flat, Pic, MyErr, halves, take_ptr


# --- Sibling loop: an unrelated loop stands between the binding loop and the
# read, so the one declaration has to go before the FIRST of them.

# Free function, scalar: the second loop binds nothing of the name.
def sib_scalar() -> None:
    for i in range(2):
        v = i + 1  # tpyc: ok
    for k in range(2):
        print("sib_scalar", k)
    v = v + 1
    print("sib_scalar", v)


# Free function, @nocopy record mutated through the hoisted name after the
# sibling loop; the alias taken in the body observes the mutation.
def sib_record() -> None:
    for i in range(2):
        f = Flat(i)  # tpyc: ok
        saved = f
    for k in range(2):
        print("sib_record", k)
    f.n += 100
    print("sib_record", f.n, saved.n)


# Free function, Ptr[T] taken in the body: the pointer local must not be the
# uninitialized second declaration (that one panics on the deref).
def sib_ptr() -> None:
    pics = [Pic(100), Pic(101)]
    for p in pics:
        q = take_ptr(p)  # tpyc: ok
    for k in range(2):
        print("sib_ptr", k)
    q.bump()
    print("sib_ptr", q.n, pics[1].n)


# Free function, a tuple-unpack target and the tuple itself (int32 elements,
# copied out of the per-iteration tuple).
def sib_unpack() -> None:
    n = 40
    for i in range(2):
        n, kept = halves(n)  # tpyc: ok
        t = (n, kept)
    for k in range(2):
        print("sib_unpack", k)
    print("sib_unpack", kept, n, t)


# Free function, Box[T] (@nocopy, so a silent copy would not compile).
def sib_box() -> None:
    for i in range(2):
        b = Box(Pic(i))  # tpyc: ok
    for k in range(2):
        print("sib_box", k)
    print("sib_box", b.n)


# Free function, pointer-repr Optional and value-repr Optional. The pointer
# one binds a PRE-EXISTING element, so the post-loop mutation through it is
# observable on the source -- a copy would leave `base[1]` at 1.
def sib_optional() -> None:
    base = [Pic(0), Pic(1)]
    for i in range(2):
        p: Optional[Pic] = base[i]  # tpyc: ok
        m: Optional[int32] = i * 10
    for k in range(2):
        print("sib_optional", k)
    if p is not None:
        p.n += 1
        print("sib_optional", p.n, m, base[1].n)


# A UNION-typed local would go here; every position rejects it at lowering
# (BUGS.md#union-local-hoist-unclassified), so there is nothing to pin yet.


# Free function, a readonly alias in both arms: the const-pointer flavor.
# Parity-blind by construction -- see the top-of-file comment.
def sib_readonly(a: readonly[Pic], b: readonly[Pic]) -> None:
    for i in range(2):
        if i == 0:
            v = a  # tpyc: ok
        else:
            v = b
    for k in range(2):
        print("sib_readonly", k)
    print("sib_readonly", v.n)


# Free function, a container element borrowed in the body and grown after the
# sibling loop -- the growth lands in the container, not in a copy.
def sib_container_elem() -> None:
    xs = [[0], [1]]
    for i in range(2):
        e = xs[i]  # tpyc: ok
    for k in range(2):
        print("sib_container_elem", k)
    e.append(9)
    print("sib_container_elem", xs)


class SibHolder:
    # Method position, sibling loop.
    def run(self) -> None:
        for i in range(2):
            f = Flat(i)  # tpyc: ok
        for k in range(2):
            print("sib_method", k)
        f.n += 100
        print("sib_method", f.n)


class SibBuilt:
    v: int32

    # Constructor position, sibling loop.
    def __init__(self) -> None:
        for i in range(2):
            f = Flat(i + 20)  # tpyc: ok
        for k in range(2):
            pass
        f.n += 1
        self.v = f.n


# Closure position, sibling loop.
def sib_closure() -> None:
    def inner() -> None:
        for i in range(2):
            f = Flat(i + 30)  # tpyc: ok
        for k in range(2):
            pass
        f.n += 1
        print("sib_closure", f.n)

    inner()


# @error_return body, sibling loop.
@error_return(MyErr)
def sib_er(k: int32) -> int32:
    for i in range(2):
        f = Flat(i + k)  # tpyc: ok
    for j in range(2):
        pass
    f.n += 1
    return f.n


def main() -> None:
    sib_scalar()
    sib_record()
    sib_ptr()
    sib_unpack()
    sib_box()
    sib_optional()
    sib_readonly(Pic(1), Pic(2))
    sib_container_elem()
    SibHolder().run()
    print("sib_ctor", SibBuilt().v)
    sib_closure()
    try:
        print("sib_error_return", sib_er(50))
    except MyErr:
        print("sib_error_return raised")
