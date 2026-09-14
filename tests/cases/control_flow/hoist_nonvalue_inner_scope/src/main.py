# A non-value local first bound in both arms of an `if` that sits INSIDE a
# loop / branch / with / try / match body hoists at that if's head (`T* f;`
# in the enclosing block, rebind slots at function top), the same render the
# function-top if takes. Likewise an `Optional[T]` local first declared in a
# loop body and rvalue-reassigned in a nested if. Every hoisted name is
# mutated after the if so a silent copy would show; the record is @nocopy.
# Destructor order of the superseded object under an in-iteration alias is
# NOT pinned here: BUGS.md#loop-if-hoist-alias-early-del.
from typing import Optional, Protocol
from tpy import int32, Own, ReturnException, dynamic, error_return, nocopy


@nocopy
class Flat:
    def __init__(self, n: int32) -> None:
        self.n = n


class Pic:
    def __init__(self, n: int32) -> None:
        self.n = n


@dynamic
class Shape(Protocol):
    def area(self) -> int32: ...


class Sq:
    def __init__(self, s: int32) -> None:
        self.s = s

    def area(self) -> int32:
        return self.s * self.s


class Rect:
    def __init__(self, w: int32, h: int32) -> None:
        self.w = w
        self.h = h

    def area(self) -> int32:
        return self.w * self.h


class CM:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        print("with_body exit", self.n)


class MyErr(Exception, ReturnException):
    pass


def make_list() -> Own[list[int32]]:
    return [1, 2, 3]


# Free function, for body: rvalue in both arms (pointer-local + rebind slot).
def for_record() -> None:
    for i in range(3):
        if i % 2 == 0:
            f = Flat(i)  # tpyc: ok
        else:
            f = Flat(i + 10)
        f.n += 1
        g = f
        g.n += 1
        print("for_record", f.n, g.n)


# For body, list literal in both arms; the list is grown after the if.
def for_list() -> None:
    for i in range(3):
        if i % 2 == 0:
            xs = [i]  # tpyc: ok
        else:
            xs = [i, i]
        xs.append(7)
        print("for_list", xs)


# For body, an Own-returning call in one arm and a literal in the other.
def for_own_call(flag: bool) -> None:
    for i in range(2):
        if flag:
            items = make_list()  # tpyc: ok
        else:
            items = [i]
        items.append(99)
        print("for_own_call", items)


# While body, one binding arm plus `continue` (the OPTIONAL_STORAGE flavor).
def while_one_arm() -> None:
    i = 0
    while i < 4:
        i += 1
        if i % 2 == 0:
            f = Flat(i)  # tpyc: ok
        else:
            continue
        f.n += 1
        print("while_one_arm", f.n)


# The if nested in another if's arm (the outer if hoists nothing).
def nested_if(a: bool, b: bool) -> None:
    if a:
        if b:
            f = Flat(1)  # tpyc: ok
        else:
            f = Flat(2)
        f.n += 1
        print("nested_if", f.n)
    else:
        print("nested_if none")


# The loop-hoisted name is declared again in the sibling arm of the outer if.
def sibling_decl(a: bool) -> None:
    if a:
        for i in range(2):
            if i == 0:
                f = Flat(i)  # tpyc: ok
            else:
                f = Flat(i + 10)
            f.n += 1
            print("sibling_decl", f.n)
    else:
        f = Flat(5)
        f.n += 1
        print("sibling_decl", f.n)


# The loop-hoisted name is bound again after the loop (a fresh decl there).
def post_loop_redecl() -> None:
    for i in range(2):
        if i == 0:
            f = Flat(i)  # tpyc: ok
        else:
            f = Flat(i + 10)
        print("post_loop_redecl", f.n)
    f = Flat(99)
    f.n += 1
    print("post_loop_redecl", f.n)


# @dynamic protocol local in a loop: `Base* s;` + per-arm adapter slots. Rvalue
# arms only: an lvalue arm copies the source (BUGS.md#dyn-local-lvalue-bind-copies).
def dyn_protocol() -> None:
    for i in range(3):
        if i % 2 == 0:
            s: Shape = Sq(i + 1)  # tpyc: ok
        else:
            s = Rect(i, 2)
        print("dyn_protocol", s.area())


# Pointer-repr Optional bound in both arms (`None` in one).
def optional_both_arms() -> None:
    for i in range(3):
        if i % 2 == 0:
            p: Optional[Pic] = Pic(i)  # tpyc: ok
        else:
            p = None
        if p is not None:
            p.n += 1
            print("optional_both_arms", p.n)
        else:
            print("optional_both_arms none")


# Optional local first declared in the loop body, rvalue-reassigned in a nested if.
def optional_slot_loop() -> None:
    for i in range(3):
        p: Optional[Pic] = None  # tpyc: ok
        if i % 2 == 0:
            p = Pic(i)
        q = p
        if q is not None:
            q.n += 5
        if p is not None:
            print("optional_slot_loop", p.n)
        else:
            print("optional_slot_loop none")


# Optional local first declared in the loop body from a record rvalue and never
# reassigned: the F1 `__slot_N` storage is block-scoped next to the pointer.
def optional_rvalue_init() -> None:
    for i in range(2):
        p: Optional[Pic] = Pic(i)  # tpyc: ok
        if p is not None:
            p.n += 1
            print("optional_rvalue_init", p.n)


# ... the same with a list literal init, grown through the pointer.
def optional_list_init() -> None:
    for i in range(2):
        xs: Optional[list[int32]] = [i]  # tpyc: ok
        if xs is not None:
            xs.append(5)
            print("optional_list_init", xs)


# ... and the same name declared again after the loop, reseated once more.
def optional_redecl_post_loop() -> None:
    for i in range(2):
        p: Optional[Pic] = None  # tpyc: ok
        if i == 0:
            p = Pic(i)
        if p is not None:
            print("optional_redecl_post_loop", p.n)
    p: Optional[Pic] = Pic(9)
    p = Pic(10)
    if p is not None:
        p.n += 1
        print("optional_redecl_post_loop", p.n)


class Builder:
    total: int32

    # Constructor position.
    def __init__(self, k: int32) -> None:
        self.total = 0
        for i in range(2):
            if i == 0:
                f = Flat(k)  # tpyc: ok
            else:
                f = Flat(k + i)
            f.n += 1
            self.total += f.n

    # Method position.
    def add(self, k: int32) -> None:
        for i in range(2):
            if i == 0:
                f = Flat(k)  # tpyc: ok
            else:
                f = Flat(k * 2)
            f.n += 1
            self.total += f.n


# With body: sema's with-hoist claims every body-new name first, so the if
# reseats through the with's predecl rather than hoisting its own.
def with_body() -> None:
    with CM(1) as n:
        if n > 0:
            f = Flat(n)  # tpyc: ok
        else:
            f = Flat(0)
        f.n += 1
        print("with_body", f.n)


# Try body inside a loop.
def try_body() -> None:
    for i in range(2):
        try:
            if i == 0:
                f = Flat(1)  # tpyc: ok
            else:
                f = Flat(2)
            f.n += 1
            print("try_body", f.n)
        except ValueError:
            print("try_body err")


# Match arm body.
def match_arm(k: int32) -> None:
    match k:
        case 0:
            if k == 0:
                f = Flat(10)  # tpyc: ok
            else:
                f = Flat(11)
            f.n += 1
            print("match_arm", f.n)
        case _:
            print("match_arm other")


# Closure body.
def closure() -> None:
    def inner(k: int32) -> int32:
        for j in range(2):
            if j == 0:
                f = Flat(k)  # tpyc: ok
            else:
                f = Flat(k + j)
            f.n += 1
            k += f.n
        return k

    print("closure", inner(1))


# @error_return body.
@error_return(MyErr)
def error_return_body(i: int32) -> int32:
    for j in range(2):
        if j == 0:
            f = Flat(i)  # tpyc: ok
        else:
            f = Flat(i + j)
        f.n += 1
        i += f.n
    return i


def main() -> None:
    for_record()
    for_list()
    for_own_call(True)
    for_own_call(False)
    while_one_arm()
    nested_if(True, False)
    nested_if(False, False)
    sibling_decl(True)
    sibling_decl(False)
    post_loop_redecl()
    dyn_protocol()
    optional_both_arms()
    optional_slot_loop()
    optional_rvalue_init()
    optional_list_init()
    optional_redecl_post_loop()
    b = Builder(3)
    b.add(4)
    print("ctor_method", b.total)
    with_body()
    try_body()
    match_arm(0)
    match_arm(1)
    closure()
    try:
        print("error_return_body", error_return_body(1))
    except MyErr:
        print("error_return_body err")


main()
