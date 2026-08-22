"""The owning-reseat wave (mechanical residue, F3 registry): two rows.

Row A -- a method-call F1-record RVALUE at the REBIND_SLOT decl and reseat
(`cur = a.clone()` -> `Rc<Node>* cur = &__slot_1;` ...
`cur = &*(__slot_2 = nxt->clone());`): the classifier already returned
REBIND_SLOT for the rvalue-reassigned name; the eligibility gates only
admitted ctor / free-call sources.
Row B -- an OPTIONAL_TO_PTR lift whose field source hangs off an admitted
method-call receiver (`parent_ref = child.get().parent` ->
`Weak<Node>* parent_ref = ::tpy::optional_to_ptr(child.get().parent);`).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
)


def _thir_fallbacks(source, extra_lib_dirs=None):
    compiler, modules = _compile(source, extra_lib_dirs=extra_lib_dirs)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_CHAIN = (
    "from tpy import Int32\n"
    "from tplib import Rc\n"
    "class Node:\n"
    "    value: Int32\n"
    "    next: Rc[Node] | None\n"
    "    def __init__(self, value: Int32) -> None:\n"
    "        self.value = value\n"
    "        self.next = None\n"
)


class TestMethodRvalueRebindSlot:
    SRC = _CHAIN + (
        "def main() -> None:\n"
        "    a = Rc.new(Node(1))\n"
        "    b = Rc.new(Node(2))\n"
        "    a.get().next = b.clone()\n"
        "    cur = a.clone()\n"
        "    while True:\n"
        "        print(cur.get().value)\n"
        "        nxt = cur.get().next\n"
        "        if nxt is None:\n"
        "            break\n"
        "        cur = nxt.clone()\n"
        "main()\n"
    )

    def test_clone_rebind_decl_and_reseat_route(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "cur = &__slot_1;" in cpp
        assert "cur = &*(__slot_2 = nxt->clone());" in cpp

    def test_borrow_returning_method_decl_routes(self):
        # A BORROW-returning method result at a reassigned decl: the
        # decl.ptr_call_addr arm binds a pointer to the live storage
        # (`Node* cur = &(h.peek());`), and the rvalue reseat rides the
        # rebind slot -- REBIND_SLOT never claims it.
        src = (
            "from tpy import Int32, Own\n"
            "class Node:\n"
            "    value: Int32\n"
            "    def __init__(self, value: Int32) -> None:\n"
            "        self.value = value\n"
            "class Holder:\n"
            "    n: Node\n"
            "    def __init__(self, n: Own[Node]) -> None:\n"
            "        self.n = n\n"
            "    def peek(self) -> Node:\n"
            "        return self.n\n"
            "def use(h: Holder, h2: Holder) -> Int32:\n"
            "    cur = h.peek()\n"
            "    cur = Node(9)\n"
            "    return cur.value\n"
        )
        fell = _thir_fallbacks(src)
        assert not fell, fell


class TestOptPtrLiftOffMethodCallField:
    SRC = _CHAIN + (
        "def main() -> None:\n"
        "    child = Rc.new(Node(1))\n"
        "    parent_ref = child.get().next\n"
        "    if parent_ref is not None:\n"
        "        print(parent_ref.value)\n"
        "    else:\n"
        "        print(0)\n"
        "main()\n"
    )

    def test_lift_off_method_call_field_routes(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert ("Rc<Node>* parent_ref = "
                "::tpy::optional_to_ptr(child.get().next);") in cpp.replace(
                    "::tpystd::tplib::rc::", "")

    def test_free_call_receiver_field_materializes_a_slot(self):
        # A field off a FREE-call result is an RVALUE receiver, so the bare
        # lift would hand optional_to_ptr a dying temporary. The AST
        # materializes the WHOLE optional first, and THIR mirrors that slot
        # + lift pair -- the two receiver flavors take DIFFERENT renders,
        # which is what keeps the method-call row's bare lift honest.
        src = _CHAIN + (
            "from tpy import Own\n"
            "def make() -> Own[Node]:\n"
            "    return Node(5)\n"
            "def probe() -> Int32:\n"
            "    nxt = make().next\n"
            "    if nxt is not None:\n"
            "        return nxt.value\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe())\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = (hpp + cpp).replace("::tpystd::tplib::rc::", "")
        assert "std::optional<Rc<Node>> __slot_1 = make().next;" in out
        assert "Rc<Node>* nxt = ::tpy::optional_to_ptr(__slot_1);" in out

    def test_inferred_readonly_receiver_lift_stays_nonconst(self):
        # Regression (review round 2026-08-02): the AST's
        # is_const_union_source stops const propagation at a CALL node, so
        # an inferred-readonly inner method must NOT make the lift const --
        # THIR briefly spelled `const Weak<Node>*` here where the AST
        # spells non-const.
        src = _CHAIN + (
            "class Holder:\n"
            "    child: Rc[Node]\n"
            "    def __init__(self, child: Rc[Node]) -> None:\n"
            "        self.child = child.clone()\n"
            "    def probe(self) -> Int32:\n"
            "        nxt = self.child.get().next\n"
            "        if nxt is not None:\n"
            "            return nxt.value\n"
            "        return 0\n"
            "def main() -> None:\n"
            "    h = Holder(Rc.new(Node(3)))\n"
            "    print(h.probe())\n"
            "main()\n"
        )
        # Every body routes (the Rc.new rvalue arg rides the method-rvalue
        # temp row since wave 3b); the lift line pins the non-const
        # spelling byte-identically.
        hpp, cpp = _assert_routes_byte_identical(src)
        lift = next(line for line in (hpp + cpp).splitlines()
                    if "optional_to_ptr" in line)
        assert "const" not in lift

    def test_const_receiver_lift_binds_const(self):
        # The const twin: a readonly method param roots the chain const, so
        # the lift spells `const T*` on both paths.
        src = _CHAIN + (
            "from tpy import readonly\n"
            "def probe(child: readonly[Rc[Node]]) -> Int32:\n"
            "    nxt = child.get().next\n"
            "    if nxt is not None:\n"
            "        return nxt.value\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    r = Rc.new(Node(3))\n"
            "    print(probe(r))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const" in next(
            line for line in cpp.splitlines() if "optional_to_ptr" in line)


_TEMP_FIELD = (
    "from tpy import Int32, Own, copy\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32):\n        self.x = x\n"
    "class Holder:\n"
    "    value: Point | None\n"
    "    def __init__(self) -> None:\n        self.value = None\n"
    "def make_holder() -> Own[Holder]:\n"
    "    h = Holder()\n"
    "    h.value = Point(1)\n"
    "    return copy(h)  # tpyc: warning(/unnecessary copy/)\n"
)


class TestOptFieldOffRvalueReceiver:
    """A storage-form Optional FIELD read off an RVALUE receiver: the whole
    optional materializes in a slot before the receiver temporary dies."""

    def test_reseat_writes_the_slot_inline(self):
        src = _TEMP_FIELD + (
            "def probe() -> Int32:\n"
            "    v: Point | None = None\n"
            "    v = make_holder().value\n"
            "    if v is not None:\n"
            "        return v.x\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe())\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        # The rebind slot is declared EMPTY at the function top and written
        # inline -- one line, unlike the OPT_STORAGE_CALL fill-then-lift.
        assert "std::optional<Point> __slot_1;" in out
        assert ("v = ::tpy::optional_to_ptr(__slot_1 = "
                "make_holder().value);") in out

    def test_lvalue_receiver_keeps_the_bare_lift(self):
        # BOUNDARY: an LVALUE receiver's field is live storage, so it takes
        # the bare `optional_to_ptr(h.value)` -- no slot. The two receiver
        # flavors must not collapse into one render.
        src = _TEMP_FIELD + (
            "def probe(h: Holder) -> Int32:\n"
            "    v = h.value\n"
            "    if v is not None:\n"
            "        return v.x\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe(Holder()))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "::tpy::optional_to_ptr(h.value);" in out
        assert "__slot_1 = h.value" not in out

    def test_rvalue_reassigned_first_decl_keeps_rejecting(self):
        # BOUNDARY: a name DECLARED from the rvalue field AND rvalue-reseat
        # later needs the AST's two-slot pairing (an init slot plus a
        # separate rebind slot); the decl row registers only one, so the
        # shape must fall back rather than reuse the init slot.
        src = _TEMP_FIELD + (
            "def probe() -> Int32:\n"
            "    v = make_holder().value\n"
            "    v = make_holder().value\n"
            "    if v is not None:\n"
            "        return v.x\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(probe())\n"
            "main()\n"
        )
        fell = _thir_fallbacks(src)
        assert "body:stmt.var_decl:decl.opt_slot_source" in fell, fell
