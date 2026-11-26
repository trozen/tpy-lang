# `@error_return` unwraps in a held `and` / `or` operand under a guard. An
# operand that is one unwrap is banked: its slot's type is read over the
# call alone, so the check runs only where the operand is evaluated. An
# unwrap nested inside a larger operand is in part_lowered.py.
from tpy import int32, error_return, ReturnException, readonly, auto_readonly
from fixtures import head, mk, total


class E(Exception, ReturnException):
    pass


@error_return(E)
def er_i(n: int32) -> int32:
    print("unwraps er_i", n)
    if n < 0:
        raise E()
    return n


@error_return(E)
def er_s(n: int32) -> str:
    print("unwraps er_s", n)
    if n < 0:
        raise E()
    return "s-heap-string-0123456789" * n


@error_return(E)
def ident[T](x: T, n: int32) -> T:
    print("unwraps ident", n)
    if n < 0:
        raise E()
    return x


class H:
    tag: str

    def __init__(self) -> None:
        self.tag = "h-heap-tag-long-string-0123456789"

    @readonly
    @error_return(E)
    def count(self, n: int32) -> int32:
        print("unwraps count", n)
        if n < 0:
            raise E()
        return n

    @auto_readonly
    @error_return(E)
    def label(self, n: int32) -> str:
        print("unwraps label", n)
        if n < 0:
            raise E()
        return self.tag[:n]


def word(n: int32) -> str:
    return "w-heap-string-0123456789" * n


def shout(n: int32) -> int32:
    print("unwraps shout", n)
    return n


# -- The operand is the unwrap: banked under its guard.

@error_return(E)
def b_int(g: bool, n: int32) -> int32:
    return (er_i(n) or 5) if g else 0  # tpyc: ok


@error_return(E)
def b_str(g: bool, n: int32) -> str:
    return (er_s(n) or "d") if g else "e"  # tpyc: ok


@error_return(E)
def b_gen(g: bool, a: int32, n: int32) -> int32:
    # A generic callee at a value type.
    return (ident(a, n) or 9) if g else 1  # tpyc: ok


@error_return(E)
def b_const(g: bool, h: H, n: int32) -> str:
    # A readonly and an `@auto_readonly` method on a parameter inferred
    # const: C++ picks the const overload, the query spells no type.
    c = (h.count(n) or 5) if g else 0  # tpyc: ok
    return str(c) + "/" + ((h.label(n) or "d") if g else "e")  # tpyc: ok


@error_return(E)
def b_arg(g: bool, n: int32) -> int32:
    # The call's argument holds a temp of its own: declared once, read by
    # the slot's type query and by its fill.
    return (er_i(n * len(word(n) or "z")) or 5) if g else 0  # tpyc: ok


def named_goto(g: bool, goto: int32) -> int32:
    # A parameter spelled like a C++ jump: banked under its guard.
    return (shout(goto) or 9) if g else 3  # tpyc: ok


def er_root(n: int32) -> None:
    # In a `try`, beside a `*args` array: a fresh root whose value checks
    # an unwrap is not bound ahead of the array (its check would jump past
    # it); it is read inline, as before
    # (BUGS.md#held-operand-nested-unwrap-eager). Only lengths are read.
    try:
        r = total(head(mk("unwraps-root", er_i(n)).name))  # tpyc: ok
        print("unwraps-root", r)
    except E:
        print("unwraps-root raised", n)


class Bag:
    @error_return(E)
    def __contains__(self, n: int32) -> bool:
        print("unwraps contains", n)
        if n < 0:
            raise E()
        return n > 0


def er_dunder(n: int32) -> None:
    # The same through an implicit `@error_return` `__contains__`: the
    # root whose value checks it is read inline, as before.
    bag = Bag()
    try:
        r = total(head(mk("unwraps-dunder", 1 if n in bag else 2).name))  # tpyc: ok
        print("unwraps-dunder", r)
    except E:
        print("unwraps-dunder raised", n)


def main() -> None:
    h = H()
    # Guard taken, a raising call behind a false guard (not called),
    # raising with the guard taken, and a zero taking the fallback. Each
    # result is bound first: the call prints, and a raise leaves no
    # partial line.
    for g, n in [(True, 2), (False, -1), (True, -1), (True, 0)]:
        try:
            r_int = b_int(g, n)
            print("unwraps-b int", r_int)
        except E:
            print("unwraps-b int raised", g, n)
        try:
            r_str = b_str(g, n)
            print("unwraps-b str", r_str)
        except E:
            print("unwraps-b str raised", g, n)
        try:
            r_gen = b_gen(g, 4, n)
            print("unwraps-b gen", r_gen)
        except E:
            print("unwraps-b gen raised", g, n)
        try:
            r_const = b_const(g, h, n)
            print("unwraps-b const", r_const)
        except E:
            print("unwraps-b const raised", g, n)
        try:
            r_arg = b_arg(g, n)
            print("unwraps-b arg", r_arg)
        except E:
            print("unwraps-b arg raised", g, n)
    r_goto = named_goto(False, 5)
    r_goto_taken = named_goto(True, 0)
    print("unwraps-a goto", r_goto, r_goto_taken)
    er_root(2)
    er_root(-1)
    er_dunder(2)
    er_dunder(-1)
