"""A fresh callable refused at a generic class's `Callable[[], T]` member
names the member as its class declares it, whichever path the call takes:
a static, a class or a generic method analyzes a FunctionInfo of its own,
an inherited field is declared by its base, and a lambda a generic method's
inference pass types first must not leave an unnamed refusal behind."""

import re

import pytest

from ..diagnostics import SemanticError
from ..thir.testutil import _compile


SOURCE = '''from typing import Callable
from tpy import Own, int32
class P:
    def __init__(self, n: int32) -> None:
        self.n = n
G = P(0)
def lend() -> P:
    return G
def mk() -> Own[P]:
    return P(1)
class C[T]:
    f: Callable[[], T]
    def __init__(self, f: Callable[[], T]) -> None:
        self.f = f
    @staticmethod
    def s_call(f: Callable[[], T]) -> None:
        pass
    @classmethod
    def c_call(cls, f: Callable[[], T]) -> None:
        pass
    def g_call[U](self, f: Callable[[], T], u: U) -> None:
        pass
class D(C[P]):
    def __init__(self) -> None:
        super().__init__(lend)
def main() -> None:
    c = C[P](lend)
    d = D()
    SUBJECT
main()
'''


def _member(label: str) -> str:
    return (f"but {re.escape(label)} \\(Callable\\[\\[\\], T\\]\\) returns a "
            f"borrow; declare {re.escape(label)} as "
            f"Callable\\[\\[\\], Own\\[T\\]\\]")


@pytest.mark.parametrize("subject,label", [
    ("C[P].s_call(mk)", "parameter 'f' of C.s_call"),
    ("C[P].c_call(mk)", "parameter 'f' of C.c_call"),
    ("c.g_call(mk, 1)", "parameter 'f' of C.g_call"),
    ("c.g_call(lambda: P(4), 1)", "parameter 'f' of C.g_call"),
    ("C[P].s_call(lambda: P(4))", "parameter 'f' of C.s_call"),
    ("d.f = mk", "'C.f'"),
])
def test_fresh_callable_names_class_member(subject: str, label: str) -> None:
    with pytest.raises(SemanticError, match=_member(label)):
        _compile(SOURCE.replace("SUBJECT", subject))


ROOTS = '''from typing import Callable
from tpy import Own, Ptr, int32
class P:
    def __init__(self, n: int32) -> None:
        self.n = n
G = P(0)
def lend() -> P:
    return G
def mk() -> Own[P]:
    return P(1)
class Lender:
    p: P
    def __init__(self) -> None:
        self.p = P(2)
    def __call__(self) -> Ptr[P]:
        return self.p
class C[T]:
    f: Callable[[], T]
    o: Callable[[], T] | None
    def __init__(self, f: Callable[[], T]) -> None:
        self.f = f
        self.o = None
    def run_all(self, *fs: Callable[[], T]) -> None:
        pass
    def set(self, f: Own[Callable[[], T]] | None) -> None:
        pass
class D(C[P]):
    def __init__(self) -> None:
        super().__init__(lend)
class Box[T]:
    x: T
    def __init__(self, x: T) -> None:
        self.x = x
def main() -> None:
    q = P(1)
    c = C[P](lend)
    lp: Callable[[], Ptr[P]] = Lender()
    SUBJECT
main()
'''


@pytest.mark.parametrize("subject,message", [
    # A capture, a stateful nested def and a call no signature roots are
    # refused naming the member, as the fresh result is.
    ("c.f = lambda: q",
     "points into 'q', which it captures, not into an argument: a borrow "
     "returned through 'C.f' (Callable[[], T]) must point into its "
     "arguments. Take the value as a parameter, pass the lambda to an Fn "
     "parameter, or declare 'C.f' as Callable[[], Own[T]] to return a copy"),
    ("def inner() -> P:\n        q.n += 1\n        return G\n    c.f = inner",
     "'inner' cannot be passed as 'C.f' (Callable[[], T]): it captures 'q'"),
    ("c.f = lambda: lp()",
     "through 'lp', whose signature cannot promise it points only into its "
     "arguments: a borrow returned through 'C.f' (Callable[[], T]) must "
     "point into its arguments. Pass the lambda to an Fn parameter, or "
     "declare 'C.f' as Callable[[], Own[T]] to return a copy"),
    # An optional member keeps its `| None` in the remedy.
    ("c.o = mk",
     "'mk' returns Own[P], but 'C.o' (Callable[[], T] | None) returns a "
     "borrow; declare 'C.o' as Callable[[], Own[T]] | None"),
    # ... and the `Own` an optional parameter holds its callable under.
    ("c.set(mk)",
     "but parameter 'f' of C.set (Own[Callable[[], T]] | None) returns a "
     "borrow; declare parameter 'f' of C.set as "
     "Own[Callable[[], Own[T]]] | None"),
    # A `*args` member is named at the element each argument meets.
    ("c.run_all(mk)",
     "but parameter 'fs' of C.run_all (Callable[[], T]) returns a borrow; "
     "declare parameter 'fs' of C.run_all as Callable[[], Own[T]]"),
    # A bare `T` the class spells is no callable member: the refusal
    # names the slot as instantiated.
    ("Box[Callable[[], P]](mk)",
     "'mk' cannot be passed as Callable[[], P]: it returns Own[P], but "
     "Callable[[], P] returns a borrow; declare Callable[[], Own[P]]"),
])
def test_member_refusal_text(subject: str, message: str) -> None:
    with pytest.raises(SemanticError, match=re.escape(message)):
        _compile(ROOTS.replace("SUBJECT", subject))


def test_super_init_names_generic_base_parameter() -> None:
    src = ROOTS.replace("super().__init__(lend)", "super().__init__(mk)")
    with pytest.raises(SemanticError, match=_member(
            "parameter 'f' of C.__init__")):
        _compile(src.replace("SUBJECT", "pass"))
