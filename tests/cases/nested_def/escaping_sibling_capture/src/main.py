# An escaping closure carries the nested defs it captures along: they escape
# too and own their captures (moved at last use, copied with a warning).
from typing import Callable
from tpy import Own


class R:
    def __init__(self, v: int) -> None:
        self.v = v


class Holder:
    fs: list[Callable[[], int]]

    def __init__(self) -> None:
        self.fs = []


class Hold:
    f: Callable[[], int]

    def __init__(self, f: Callable[[], int]) -> None:
        self.f = f


G: list[Callable[[], int]] = []


def clobber(d: int) -> int:
    # Reuses the stack a dead frame left behind, so a dangling capture reads
    # garbage instead of the value it had.
    xs = [d * 3 + i for i in range(40)]
    a = d * 7 + 1
    if d > 0:
        return clobber(d - 1) + xs[3] + a
    return xs[5] + a


def keep(sink: list[Callable[[], int]], f: Callable[[], int]) -> None:
    sink.append(f)


# Section s_scalar: a returned def reaches a local only through a sibling.
def s_scalar() -> Callable[[], int]:
    base = 7

    def helper(x: int) -> int:  # tpyc: ok
        return x + base

    def esc() -> int:  # tpyc: ok
        return helper(1)
    return esc


# Section s_list: the sibling's list local is moved in at its last use.
def s_list() -> Callable[[], int]:
    xs = [1, 2, 3]
    xs.append(10)

    def h() -> int:  # tpyc: ok
        return sum(xs)

    def esc() -> int:
        return h()
    return esc


# Section s_record: same for a record local.
def s_record() -> Callable[[], int]:
    r = R(5)
    r.v = 50

    def h() -> int:  # tpyc: ok
        return r.v

    def esc() -> int:
        return h()
    return esc


# Section s_param: a value parameter read through a sibling.
def s_param(x: float) -> Callable[[], float]:
    def h() -> float:  # tpyc: ok
        return x * 2.0

    def esc() -> float:
        return h()
    return esc


# Section s_two: two levels of siblings, esc -> h1 -> h2 -> local.
def s_two() -> Callable[[], int]:
    base = 9

    def h2() -> int:
        return base

    def h1() -> int:
        return h2() + 1

    def esc() -> int:
        return h1() + 1
    return esc


# Section s_lambda_esc: a returned lambda calling a sibling def.
def s_lambda_esc() -> Callable[[], int]:
    base = 11

    def h() -> int:
        return base

    return lambda: h() + 1  # tpyc: ok


# Section s_lambda_append: a lambda appended to a list calls a sibling def.
def s_lambda_append(out: list[Callable[[], int]]) -> None:
    base = 21

    def h() -> int:
        return base

    out.append(lambda: h() * 2)


# Section e_list: escaping through an append.
def e_list(out: list[Callable[[], int]]) -> None:
    xs = [1, 2, 30]

    def h() -> int:
        return sum(xs)

    def esc() -> int:
        return h()
    out.append(esc)


# Section e_field: escaping into a field.
def e_field(hd: Holder) -> None:
    base = 41

    def h() -> int:
        return base

    def esc() -> int:
        return h() + 1
    hd.fs.append(esc)


# Section e_global: escaping into a module global.
def e_global() -> None:
    base = 51

    def h() -> int:
        return base

    def esc() -> int:
        return h() + 1
    G.append(esc)


# Section e_tuple: escaping as a tuple element.
def e_tuple() -> tuple[int, Callable[[], int]]:
    base = 61

    def h() -> int:
        return base

    def esc() -> int:
        return h() + 1
    return (0, esc)


# Section e_passed: escaping through a function that stores it.
def e_passed(out: list[Callable[[], int]]) -> None:
    base = 71

    def h() -> int:
        return base

    def esc() -> int:
        return h() + 1
    keep(out, esc)


# Section p_ref_param: a list parameter stays shared, so a later append by
# the caller is seen.
def p_ref_param(xs: list[int]) -> Callable[[], int]:
    def h() -> int:
        return sum(xs)

    def esc() -> int:
        return h()
    return esc


class C:
    def __init__(self, v: int) -> None:
        self.v = v

    # Section p_method: the sibling owns the local and refers to the
    # receiver, so a later write to the receiver is seen.
    def p_method(self) -> Callable[[], int]:
        k = 3

        def h() -> int:
            return self.v * 10 + k

        def esc() -> int:
            return h()
        return esc


# Section make_easing: the reproducer, value parameters through siblings.
def make_easing(x1: float, y1: float, x2: float,
                y2: float) -> Callable[[float], float]:
    def sample_x(t: float) -> float:
        return 3 * x1 * (1 - t) ** 2 * t + 3 * x2 * (1 - t) * t ** 2 + t ** 3

    def sample_y(t: float) -> float:
        return 3 * y1 * (1 - t) ** 2 * t + 3 * y2 * (1 - t) * t ** 2 + t ** 3

    def ease(progress: float) -> float:
        t = progress
        for _ in range(20):
            t = t - (sample_x(t) - progress) * 0.5
        return sample_y(t)

    return ease


# Section copied_sibling: the sibling is used after the escaping closure, so
# the closure copies it, and the list it owns with it.
def copied_sibling() -> Callable[[], int]:
    xs = [1, 2, 3]
    xs.append(4)

    def h() -> int:
        return sum(xs)

    def esc() -> int:  # tpyc: warning(/copies nested function 'h', and with it its own copy of 'xs'/)
        return h()

    print("copied_sibling local", h())
    return esc


# Section i_local: a sibling called by a closure that does not escape keeps
# referring to the frame, and sees a later append.
def i_local() -> int:
    xs = [1, 2, 3]
    base = 5

    def h() -> int:
        return sum(xs) + base

    def caller() -> int:
        return h() + 1
    xs.append(10)
    return caller()


# Section i_lambda: a lambda bound to a local and called in place does not
# make the sibling escape, so its nonlocal write is allowed and seen.
def i_lambda() -> int:
    base = 6

    def h() -> int:
        nonlocal base
        base += 1
        return base

    f: Callable[[], int] = lambda: h() + 1
    r = f()
    return r * 100 + base


# Section own_used_after: an Own parameter the body returns after the
# escaping sibling reads it is copied into the sibling, not moved out.
def own_used_after(x: Own[R]) -> Own[R]:
    def g() -> int:  # tpyc: warning(/Escaping closure 'g' copies local 'x'/)
        return x.v

    h = Hold(lambda: g())  # tpyc: warning(/copies nested function 'g' when it is created, and with it its own copy of 'x'$/)
    print("own_used_after holder", h.f())
    return x  # tpyc: warning(/copies R into owned storage/)


# Section own_last_use: the escaping sibling is the Own parameter's last
# use, so the parameter is moved into `g`; the lambda then copies `g`, and
# the record with it (BUGS.md#escaping-lambda-capture-never-moves).
def own_last_use(x: Own[R]) -> Callable[[], int]:
    def g() -> int:  # tpyc: ok
        return x.v

    return lambda: g() + 1  # tpyc: warning(/copies nested function 'g' when it is created, and with it its own copy of 'x'$/)


# Section bigint_param: a BigInt parameter read through a sibling is copied.
def bigint_param(x: int) -> Callable[[], int]:
    def h() -> int:  # tpyc: warning(/nested function 'h' copies 'x' when it is created/)
        return x + 1

    def esc() -> int:
        return h()
    return esc


# Section per_arm: one same-named def per arm, each returned from its arm. The
# hedged copy warning is accepted: a closure inside a block copies what it
# could move (BUGS.md#closure-captured-name-moved-at-last-use).
def per_arm(flag: bool) -> Callable[[], int]:
    base = [7]
    if flag:
        def esc() -> int:  # tpyc: warning(/copies local 'base' \(may be used again after the closure definition/)
            return base[0] + 10
        return esc
    else:
        def esc() -> int:  # tpyc: warning(/copies local 'base' \(may be used again after the closure definition/)
            return base[0] + 20
        return esc


# Section lambda_used_after: the body calls the sibling again after the
# escaping lambda copied it -- proven at the top level; inside an `if` the
# top-level rule cannot prove it, so the warning hedges.
def lambda_used_after(out: list[Callable[[], int]], flag: bool) -> int:
    xs = [1, 2, 3]

    def h() -> int:
        return sum(xs)
    out.append(lambda: h() + 1)  # tpyc: warning(/copies nested function 'h' when it is created, and with it its own copy of 'xs'; 'h' is used after it; a change made through one copy is not seen through the other/)
    if flag:
        out.append(lambda: h() * 2)  # tpyc: warning(/copies nested function 'h' when it is created, and with it its own copy of 'xs'; 'h' may be used again after it$/)
    return h()


# Section lambda_last_use: an escaping lambda copies the sibling at its last
# use, which only costs (nothing else reads the sibling).
def lambda_last_use() -> Callable[[], int]:
    xs = [1, 2, 3]

    def h() -> int:
        return sum(xs)
    return lambda: h() + 1  # tpyc: warning(/lambda at line \d+ copies nested function 'h' when it is created, and with it its own copy of 'xs'$/)


# Section loop_def / loop_lambda: a closure in a `for` body captures a list
# the next pass reads again, so each pass copies it (never moves it out).
def loop_def(fs: list[Callable[[], int]]) -> None:
    xs = [1, 2]
    xs.append(3)
    for i in range(3):
        def f() -> int:  # tpyc: warning(/Escaping closure 'f' copies local 'xs' \(may be used again after the closure definition/)
            return len(xs)
        fs.append(f)


def loop_lambda(fs: list[Callable[[], int]]) -> None:
    xs = [1, 2]
    xs.append(3)

    def h() -> int:
        return len(xs)
    for i in range(3):
        fs.append(lambda: h() * 10)  # tpyc: warning(/copies nested function 'h' when it is created, and with it its own copy of 'xs'; 'h' may be used again after it$/)


# Section lambda_header: a lambda in an `if` header, the sibling called in
# the body after it (hedged: the top-level rule does not follow the header).
def keep_one(out: list[Callable[[], int]], f: Callable[[], int]) -> bool:
    out.append(f)
    return True


def lambda_header(out: list[Callable[[], int]]) -> int:
    xs = [1, 2, 3]

    def h() -> int:
        return len(xs)
    if keep_one(out, lambda: h()):  # tpyc: warning(/copies nested function 'h' when it is created, and with it its own copy of 'xs'; 'h' may be used again after it$/)
        return h()
    return 0


# Section per_handler: one same-named def in a `try` body and one in its
# handler, separate blocks of one `try`.
def per_handler(flag: bool) -> int:
    try:
        def h() -> int:  # tpyc: ok
            return 1
        if flag:
            raise ValueError("x")
        return h()
    except ValueError:
        def h() -> int:  # tpyc: ok
            return 2
        return h()


# Section block_sibling_hedged: an escaping def inside a block copies the
# sibling it calls, and the list that sibling holds, with the hedged wording.
def block_sibling_hedged(out: list[Callable[[], int]], flag: bool) -> None:
    xs = [1, 2]
    xs.append(3)

    def h() -> int:
        return len(xs)
    if flag:
        def f() -> int:  # tpyc: warning(/Escaping closure 'f' copies nested function 'h', and with it its own copy of 'xs' \('h' may be used again after the closure definition, preventing a move\)\. Reorder/)
            return h() + 1
        out.append(f)


# Section dead_sibling: an earlier block-local `h` capturing `xs` is not the
# `h` called after the escaping def, so `xs` still moves into `esc`.
def dead_sibling(flag: bool) -> Callable[[], int]:
    xs = [1, 2, 3]
    xs.append(4)
    if flag:
        def h() -> int:
            return len(xs)
        print("dead_sibling inner", h())

    def esc() -> int:  # tpyc: ok
        return len(xs)

    def h() -> int:
        return 7
    print("dead_sibling scope", h())
    return esc


# Section per_case: one same-named def per `match` case.
def per_case(k: int) -> int:
    match k:
        case 1:
            def h() -> int:  # tpyc: ok
                return 1
            return h()
        case _:
            def h() -> int:  # tpyc: ok
                return 2
            return h()


def main() -> None:
    f = s_scalar()
    clobber(30)
    print("s_scalar", f())
    f = s_list()
    clobber(30)
    print("s_list", f())
    f = s_record()
    clobber(30)
    print("s_record", f())
    g = s_param(21.0)
    clobber(30)
    print("s_param", g())
    f = s_two()
    clobber(30)
    print("s_two", f())
    f = s_lambda_esc()
    clobber(30)
    print("s_lambda_esc", f())
    out: list[Callable[[], int]] = []
    s_lambda_append(out)
    e_list(out)
    hd = Holder()
    e_field(hd)
    e_global()
    t = e_tuple()
    e_passed(out)
    clobber(30)
    print("s_lambda_append", out[0]())
    print("e_list", out[1]())
    print("e_field", hd.fs[0]())
    print("e_global", G[0]())
    print("e_tuple", t[1]())
    print("e_passed", out[2]())
    ys = [1, 1]
    f = p_ref_param(ys)
    ys.append(100)
    clobber(30)
    print("p_ref_param", f())
    c = C(4)
    f = c.p_method()
    c.v = 7
    clobber(30)
    print("p_method", f())
    ease = make_easing(0.0, 1.6, 1.0, 0.25)
    clobber(30)
    print("make_easing", round(ease(0.3), 9), round(ease(0.7), 9))
    store: list[Callable[[float], float]] = []
    for k in range(3):
        store.append(make_easing(0.1 * k, 1.0, 0.5, 0.2 * k))
    clobber(10)
    print("make_easing store", [round(e(0.5), 9) for e in store])
    f = copied_sibling()
    clobber(30)
    print("copied_sibling", f())
    print("i_local", i_local())
    print("i_lambda", i_lambda())
    q = own_used_after(R(4))
    clobber(30)
    print("own_used_after", q.v)
    f = own_last_use(R(5))
    clobber(30)
    print("own_last_use", f())
    f = bigint_param(41)
    clobber(30)
    print("bigint_param", f())
    f1 = per_arm(True)
    f2 = per_arm(False)
    f5 = lambda_last_use()
    clobber(30)
    print("per_arm", f1(), f2())
    print("lambda_last_use", f5())
    out3: list[Callable[[], int]] = []
    n = lambda_used_after(out3, True)
    clobber(30)
    print("lambda_used_after", n, out3[0](), out3[1]())
    fs: list[Callable[[], int]] = []
    loop_def(fs)
    loop_lambda(fs)
    out4: list[Callable[[], int]] = []
    m = lambda_header(out4)
    clobber(30)
    print("loop", [f() for f in fs])
    print("lambda_header", m, out4[0]())
    print("per_handler", per_handler(True), per_handler(False))
    print("per_case", per_case(1), per_case(2))
    out5: list[Callable[[], int]] = []
    block_sibling_hedged(out5, True)
    f6 = dead_sibling(True)
    clobber(30)
    print("block_sibling_hedged", out5[0]())
    print("dead_sibling", f6())


main()
