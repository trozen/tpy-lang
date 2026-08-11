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

    def test_free_call_receiver_field_keeps_rejecting(self):
        # The lift arm admits METHOD-call receivers only: a field off a
        # FREE-call result could hand optional_to_ptr a dying temporary,
        # so that shape must keep falling back.
        src = _CHAIN + (
            "from tpy import Own\n"
            "def make() -> Own[Node]:\n"
            "    return Node(5)\n"
            "def probe() -> Int32:\n"
            "    nxt = make().next\n"
            "    if nxt is not None:\n"
            "        return nxt.value\n"
            "    return 0\n"
        )
        fell = _thir_fallbacks(src)
        assert "body:stmt.var_decl:decl.opt_slot_source" in fell, fell

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
