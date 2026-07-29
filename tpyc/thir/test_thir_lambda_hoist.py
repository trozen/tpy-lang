"""THIR must fall back for a rebind slot a lambda-rendered body cannot reach.

THIR emits nested-def bodies at the enclosing body's level, so a slot needed
inside the lambda would be declared outside its capture list. The AST path
rejects these with a diagnostic (`use_rebind_slot` compares the slot's owning
hoist scope); THIR has no runtime equivalent, so `_rejects_lambda_hoist` is its
entire protection -- and an error-diagnostic test case cannot pin it, because
those never exercise the THIR overlay.
"""

from .testutil import _fn, _lower_ctor, _lower_ctx

_POINT = """from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


"""


def test_nested_def_reserving_its_own_slot_falls_back():
    """The enclosing `p` is load-bearing, not decoration: the prescan's rebind
    facts do not recurse into nested defs, so `inner`'s local is classified as
    slot-backed only because the name COLLIDES with an rvalue-reassigned one
    outside (filed in BUGS.md). The nested def must also come BEFORE that outer
    binding, or sema rejects the inner assignment as a missing `nonlocal`. Drop
    the collision and there is no slot to misplace -- so this test changes
    meaning when that defect is fixed."""
    thir = _lower_ctx(_POINT + """def outer(k: Int32) -> Int32:
    def inner(n: Int32) -> Int32:
        p = Point(n)
        if n > 0:
            p = Point(n * 10)
        return p.x

    p = Point(0)
    p = Point(1)
    return inner(k) + p.x
""")
    assert _fn(thir, "outer") is None


def test_nested_def_consuming_an_enclosing_slot_falls_back():
    """The `nonlocal` half: the slot is reserved in `outer` and consumed inside
    the lambda, so its declaration sits outside the capture list."""
    thir = _lower_ctx(_POINT + """def outer() -> Int32:
    p = Point(1)
    p = Point(100)

    def bump() -> None:
        nonlocal p
        p = Point(200)

    bump()
    return p.x
""")
    assert _fn(thir, "outer") is None


def test_ctor_nested_def_falls_back():
    ctor = _lower_ctor(_POINT + """class Holder:
    v: Int32

    def __init__(self, k: Int32) -> None:
        def inner(n: Int32) -> Int32:
            p = Point(n)
            if n > 0:
                p = Point(n * 10)
            return p.x

        p = Point(0)
        p = Point(1)
        self.v = inner(k) + p.x
""", "Holder")
    assert ctor is None


def test_nested_def_without_a_slot_still_routes():
    """The inverse: no rebind slot anywhere, so nothing blocks lowering."""
    thir = _lower_ctx(_POINT + """def outer(k: Int32) -> Int32:
    def double(n: Int32) -> Int32:
        return n * 2

    return double(k)
""")
    assert _fn(thir, "outer") is not None
