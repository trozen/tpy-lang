# tpy: ext_module
# CPython extension over the str boundary: str params/returns marshal by copy.
# str is immutable, so the boundary copy is unobservable; the divergent cases (a
# non-str arg -> TypeError, a lone-surrogate str -> UnicodeEncodeError) are
# enforced only by the compiled .so and live in ext_checks.py.
from tpy.extern import export


@export
def echo(s: str) -> str:
    return s


@export
def shout(s: str) -> str:
    return s.upper()


@export
def greet(name: str) -> str:
    return "hello, " + name
