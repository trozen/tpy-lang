"""Where a rebind slot a lambda-rendered body needs gets declared.

A body the nested def reserves for ITSELF drains at the lambda's own prologue
(`_emit_nested_def`, the AST's `nested_hoist_scope`), so it lands inside the
capture list and routes. A slot the ENCLOSING scope reserved and the lambda
CONSUMES (a `nonlocal` rebind) is declared outside that capture list: the AST
rejects it with a diagnostic (`use_rebind_slot` compares the slot's owning hoist
scope), THIR has no runtime equivalent, so `rejects_cross_scope_rebind` is its
entire protection.

The nested def is one of two lambda-rendered bodies; the simple-generator
peephole is the other, pinned in `test_thir_simple_gen.py`.
"""

from .testutil import (_assert_routes_byte_identical, _fn, _lower_ctor,
                       _lower_ctx)

_POINT = """from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


"""


def test_nested_def_reserving_its_own_slot_routes():
    """The lambda's own slot drains at the lambda's prologue -- byte-identity
    against the AST is what proves the placement (a slot emitted at the
    enclosing prologue would be outside the capture list, and the C++ would not
    even compile).

    The enclosing `p` is load-bearing, not decoration: the prescan's rebind
    facts do not recurse into nested defs, so `inner`'s local is classified as
    slot-backed only because the name COLLIDES with an rvalue-reassigned one
    outside (filed in BUGS.md). The nested def must also come BEFORE that outer
    binding, or sema rejects the inner assignment as a missing `nonlocal`. Drop
    the collision and there is no slot to place -- so this test changes meaning
    when that defect is fixed."""
    _assert_routes_byte_identical(_POINT + """def outer(k: Int32) -> Int32:
    def inner(n: Int32) -> Int32:
        p = Point(n)
        if n > 0:
            p = Point(n * 10)
        return p.x

    p = Point(0)
    p = Point(1)
    return inner(k) + p.x
""")


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


def test_ctor_nested_def_routes():
    """The constructor entry point drains the lambda's own slot the same way."""
    _assert_routes_byte_identical(_POINT + """class Holder:
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


def use(k: Int32) -> Int32:
    return Holder(k).v
""")


def test_nested_def_branch_first_decl_still_falls_back():
    """Residue, deliberately conservative: a local first declared in an `if`
    rides `THIRIf.hoist_slots`, whose owner names `_slot_owning_name` cannot
    read (a THIRIf has no `.name`), so the shadow exemption does not see it and
    the enclosing-slot disjunct still fires. Placement-safe either way -- the
    drain is unconditional -- so this is a missed flip, not a hazard.

    tpyc/test_compiler.py's ratchet-fires pin uses this same shape as its
    un-migrated fixture; swap that one too when this flips."""
    thir = _lower_ctx(_POINT + """def outer(k: Int32) -> Int32:
    def inner(n: Int32) -> Int32:
        if n > 0:
            p = Point(n)
        else:
            p = Point(0)
        p = Point(n * 10)
        return p.x

    p = Point(0)
    p = Point(1)
    return inner(k) + p.x
""")
    assert _fn(thir, "outer") is None


def test_nested_def_without_a_slot_still_routes():
    """The inverse: no rebind slot anywhere, so nothing blocks lowering."""
    thir = _lower_ctx(_POINT + """def outer(k: Int32) -> Int32:
    def double(n: Int32) -> Int32:
        return n * 2

    return double(k)
""")
    assert _fn(thir, "outer") is not None
