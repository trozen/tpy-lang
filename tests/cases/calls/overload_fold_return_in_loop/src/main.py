# The @overload fold of an `if` chain or a `match`: the selected arm lowers
# flat in the enclosing block, and only a top-level terminating arm truncates.
from typing import Iterator, Protocol, overload
from tpy import dynamic


class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x


class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y


# in_loop / in_branch: a fold nested in a loop / dynamic `if` must not truncate
# the code reachable after that compound (the trailing return was dropped).
@overload
def in_loop(v: A, k: int) -> int: ...
@overload
def in_loop(v: B, k: int) -> int: ...
def in_loop(v: A | B, k: int) -> int:
    while k > 0:
        if isinstance(v, A):
            return v.x + k
        else:
            return v.y - k
    return 0            # reachable when k <= 0 -- must not be truncated


@overload
def in_branch(v: A, gate: bool) -> int: ...
@overload
def in_branch(v: B, gate: bool) -> int: ...
def in_branch(v: A | B, gate: bool) -> int:
    if gate:
        if isinstance(v, A):
            return v.x
        else:
            return v.y
    return -1           # reachable when gate is False -- must not be truncated


# toplevel: the inverse guard, a top-level fold still truncates.
@overload
def toplevel(v: A) -> int: ...
@overload
def toplevel(v: B) -> int: ...
def toplevel(v: A | B) -> int:
    if isinstance(v, A):
        return v.x      # A-stub folds True here and truncates the tail below
    return v.y


# dyn_local: a @dynamic protocol local and a generator local declared in a
# statically-true folded arm.
@dynamic
class Shape(Protocol):
    def area(self) -> int: ...


class Sq:
    def __init__(self, n: int) -> None:
        self.n = n

    def area(self) -> int:
        return self.n * self.n


def nums() -> Iterator[int]:
    yield 1
    yield 2


@overload
def dyn_local(x: int) -> int: ...
@overload
def dyn_local(x: str) -> int: ...
def dyn_local(x: int | str) -> int:
    if isinstance(x, int):
        s: Shape = Sq(x)    # tpyc: ok -- declared in the folded arm
        it = nums()
        t = 0
        for v in it:
            t += v
        return s.area() + t
    return 0


# both_arms: a name declared in both folded arms (a sema hoist) and read after
# the if, mutated in place through a borrowing call.
def bump(a: A) -> None:
    a.x += 1


@overload
def both_arms(x: int) -> int: ...
@overload
def both_arms(x: str) -> int: ...
def both_arms(x: int | str) -> int:
    if isinstance(x, int):
        y = A(x)            # tpyc: ok -- the chosen arm's decl outlives the if
    else:
        y = A(0)            # tpyc: ok
    bump(y)
    return y.x


# match_fold: a @dynamic protocol local and a generator local declared in the
# case a folded `match` selects, which lowers flat like a folded `if` arm; the
# arm writes through the subject, which must be the caller's object. (`x` is
# not read after the write: a capture aliases a value field, see
# BUGS.md#match-capture-aliases-value-field. The B specialization takes
# `B&` though its arm writes nothing, see
# BUGS.md#overload-method-union-param-nonconst.)
@overload
def match_fold(v: A) -> int: ...
@overload
def match_fold(v: B) -> int: ...
def match_fold(v: A | B) -> int:
    match v:
        case A(x=x):
            s: Shape = Sq(x)  # tpyc: ok -- declared in the selected case
            v.x += 10       # tpyc: ok -- the caller's A observes the write
            it = nums()
            t = 0
            for q in it:
                t += q
            return s.area() + t
        case B(y=y):
            return y
    return 0


# partly: the first link folds and the elif stays a run-time test.
@overload
def partly(x: int, k: int) -> int: ...
@overload
def partly(x: str, k: int) -> int: ...
def partly(x: int | str, k: int) -> int:
    if isinstance(x, str):
        return -1
    elif k > 0:             # tpyc: ok -- the live link of a folded chain
        return k
    else:
        return 0


# walrus_elif: a walrus in the live `elif` of a partly decided chain, read
# after the if.
@overload
def walrus_elif(x: int, k: int) -> int: ...
@overload
def walrus_elif(x: str, k: int) -> int: ...
def walrus_elif(x: int | str, k: int) -> int:
    if isinstance(x, str):
        return -1
    elif (m := k * 2) > 2:  # tpyc: ok -- bound in the live link, read below
        return m
    else:
        m += 1
    return m


# match_decl: a name declared in the case a folded `match` selects outlives
# the match, mutated in place through a borrowing call.
@overload
def match_decl(v: A) -> int: ...
@overload
def match_decl(v: B) -> int: ...
def match_decl(v: A | B) -> int:
    match v:
        case A(x=x):
            r = A(x)        # tpyc: ok -- the selected case's decl outlives the match
        case B(y=y):
            r = A(y * 10)
    bump(r)
    return r.x


# raises: a terminating folded arm ending in `raise` truncates the tail (which
# would not type-check under the `str` stub).
@overload
def raises(x: int) -> int: ...
@overload
def raises(x: str) -> int: ...
def raises(x: int | str) -> int:
    if isinstance(x, str):
        raise ValueError("str")
    return x                # tpyc: ok


# redecl: a name declared in the folded arm, rebound after the fold.
@overload
def redecl(x: int) -> int: ...
@overload
def redecl(x: str) -> int: ...
def redecl(x: int | str) -> int:
    if isinstance(x, int):
        y = A(x)
    else:
        y = A(0)
    y = A(y.x + 1)          # tpyc: ok
    return y.x


# nested_def: a `def` inside a statically-true arm.
@overload
def nested_def(x: int) -> int: ...
@overload
def nested_def(x: str) -> int: ...
def nested_def(x: int | str) -> int:
    if isinstance(x, int):
        def g(n: int) -> int:   # tpyc: ok
            return n + 1
        return g(x)
    return 0


def main() -> None:
    print(in_loop(A(5), 0))        # 0 -- loop skipped, trailing return
    print(in_loop(A(5), 3))        # 8
    print(in_loop(B(7), 0))        # 0
    print(in_loop(B(7), 2))        # 5
    print(in_branch(A(9), False))  # -1 -- branch skipped, trailing return
    print(in_branch(A(9), True))   # 9
    print(in_branch(B(4), False))  # -1
    print(in_branch(B(4), True))   # 4
    print(toplevel(A(2)))          # 2
    print(toplevel(B(6)))          # 6
    print("dyn_local:", dyn_local(3), dyn_local("a"))
    print("both_arms:", both_arms(3), both_arms("a"))
    a = A(3)
    print("match_fold:", match_fold(a), match_fold(B(2)), a.x)
    print("partly:", partly(1, 2), partly(1, 0), partly("a", 2))
    print("walrus_elif:", walrus_elif(1, 2), walrus_elif(1, 1),
          walrus_elif("a", 2))
    print("match_decl:", match_decl(A(3)), match_decl(B(2)))
    try:
        raises("a")
    except ValueError:
        print("raises: caught")
    print("raises:", raises(4))
    print("redecl:", redecl(3), redecl("a"))
    print("nested_def:", nested_def(3), nested_def("a"))


main()
