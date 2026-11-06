# The rebinding walrus at further statement positions: method, constructor,
# closure, `with` body, try/finally, `@error_return` body, `match` arm, loop.
from tpy import error_return, int32

from fixtures import A, Ctx, Err, H


def loop_clobber(h: H) -> None:
    x = h.a
    prev = h.b
    i = 0
    # Runs once: a second pass is the warned divergence itself.
    while i < 1:
        prev = x
        print("loop_clobber", (x := A(i)).n, prev.n)  # tpyc: warning(/will not keep the object/)
        i += 1
    print("loop_clobber", prev.n, x.n)


class M:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    def run(self, h: H) -> None:
        x = h.a
        # Method body.
        print("method", (x := h.b).n)  # tpyc: ok
        x.n += 10
        self.k = x.n
        print("method", h.a.n, h.b.n, self.k)


class C:
    k: int32

    def __init__(self, h: H) -> None:
        x = h.a
        # Constructor body.
        print("ctor", (x := h.b).n)  # tpyc: ok
        x.n += 10
        self.k = x.n
        print("ctor", h.a.n, h.b.n, self.k)


def closure(h: H) -> None:
    def inner() -> int32:
        x = h.a
        # Nested def body.
        print("closure", (x := h.b).n)  # tpyc: ok
        x.n += 10
        return x.n
    r = inner()
    print("closure", r, h.a.n, h.b.n)


def with_body(h: H) -> None:
    x = h.a
    with Ctx() as _c:
        # `with` body.
        print("with_body", (x := h.b).n)  # tpyc: ok
        x.n += 10
    print("with_body", h.a.n, h.b.n)


def try_finally(h: H) -> None:
    x = h.a
    try:
        # `try` body, read in `finally`.
        print("try_finally", (x := h.b).n)  # tpyc: ok
    finally:
        x.n += 10
    print("try_finally", h.a.n, h.b.n)


@error_return(Err)
def er(h: H) -> int32:
    x = h.a
    # `@error_return` body.
    print("error_return", (x := h.b).n)  # tpyc: ok
    x.n += 10
    return x.n


def error_return_body(h: H) -> None:
    try:
        r = er(h)
        print("error_return", r, h.a.n, h.b.n)
    except Err:
        pass


def match_arm(h: H, k: int32) -> None:
    x = h.a
    match k:
        case 1:
            # `match` arm.
            print("match_arm", (x := h.b).n)  # tpyc: ok
        case _:
            pass
    x.n += 10
    print("match_arm", h.a.n, h.b.n)


def main() -> None:
    loop_clobber(H())
    M().run(H())
    C(H())
    closure(H())
    with_body(H())
    try_finally(H())
    error_return_body(H())
    match_arm(H(), 1)
