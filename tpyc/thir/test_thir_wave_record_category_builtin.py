"""A `@builtin_type` record of RECORD category is an F1 record even when its
TypeDef carries a `cpp_formatter`.

`cpp_formatter` says the type supplies its own C++ spelling; it says nothing
about the record's SHAPE. For a RECORD-category TypeDef the resolver falls
straight through to `to_cpp()` (its other arms all key on a shape a record
does not have), so the F1 spelling invariant holds and the formatter is not
the question to ask. The categories that DO carry a divergent C++ shape
(`list` -> `std::vector`) are excluded by category, which is what the
boundary units below pin.
"""

from ..compilation_context import activate_compiler
from ..typesys import INT32, STR, make_dict, make_list
from .lower import arg_table
from .lower.checks import _PROTOCOL_ARG_SINK
from .lower.predicates import _f1_record
from .testutil import (_assert_routes_byte_identical, _compile, _entry, _fn,
                       _lower_ctx_witnessed, _thir_ctx_witnessed)

# Waker is the one RECORD-category TypeDef in the registry carrying a
# formatter, so it is the whole shape under test: a ValueType record whose
# spelling comes from the formatter rather than from `native_cpp_names`.
_SRC = (
    "from tpy import Own\n"
    "from tpy.coro import Awaitable, Poll, Waker\n"
    "class Holder:\n"
    "    w: Waker\n"
    "    def __init__(self) -> None:\n"
    "        self.w = Waker()\n"
    "    def ping(self) -> None:\n"
    "        self.w.wake()\n"
    "def fresh() -> Waker:\n"
    "    return Waker()\n"
    "def drive(w: Waker) -> None:\n"
    "    w.wake()\n"
    "def one_step[T](aw: Awaitable[T]) -> Own[Poll[T]]:\n"
    "    return aw.__poll__(Waker())\n"
)


class TestRecordCategoryBuiltinRoutes:
    def test_every_body_routes_byte_identical(self):
        # The routing half is mechanical: a whole-body fallback re-emits the
        # AST, so byte-identity alone could not fail here.
        _assert_routes_byte_identical(_SRC)

    def test_the_receiver_and_arg_faces_are_witnessed(self):
        thir, witnessed = _lower_ctx_witnessed(_SRC)
        assert _fn(thir, "fresh") is not None
        assert _fn(thir, "drive") is not None
        assert _fn(thir, "one_step") is not None
        # `recv.builtin_record` is _f1_record's builtin leg; the field-typed
        # receiver and the by-value param both reach it.
        assert witnessed.get("recv.builtin_record", 0) > 0
        # The ctor rvalue at the protocol method's by-value record slot.
        assert witnessed.get("call.value_record_arg", 0) > 0

    def test_the_member_init_ctor_routes(self):
        # The ctor lives in a member-init list, which the free-function lens
        # cannot see -- Waker is declared in another module, so the cross
        # module qualification arm is the one that spells it.
        ctx, witnessed, fallback = _thir_ctx_witnessed(_SRC)
        assert not fallback
        assert witnessed.get("ctor.cross_module", 0) > 0

    def test_the_protocol_sink_decides_the_by_value_record_slot(self):
        # Reaching the cell is the claim: the row is what admits
        # `aw.__poll__(Waker())`, and without it the body falls back.
        compiler, modules = _compile(_SRC)
        from ..codegen_cpp.context import CodeGenOptions
        compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions())
        reached = arg_table.reached(compiler)
        assert reached.get(("protocol", "value_record_rvalue"), 0) > 0

    def test_the_row_is_shared_with_the_record_method_family(self):
        # `register_sink` fails the import if a shared row name reaches a
        # different predicate, so the shape is decided in one place.
        row = next(r for r in _PROTOCOL_ARG_SINK.rows
                   if r.row == "value_record_rvalue")
        assert arg_table._ROW_FNS["value_record_rvalue"] is row.fn


class TestFormatterCarryingNonRecordStaysExcluded:
    """The boundary: a formatter on a CONTAINER/VIEW category still means a
    divergent C++ shape, so those must keep rejecting."""

    def test_f1_record_rejects_the_container_categories(self):
        # The canonical singletons, not hand-built NominalTypes: a type the
        # registry does not recognize would be rejected for its construction
        # rather than for its category, which is a pin that cannot fail.
        compiler, modules = _compile(_SRC)
        with activate_compiler(compiler):
            analyzer = compiler.modules[_entry(modules).name].analyzer
            assert not _f1_record(make_list(INT32), analyzer)
            assert not _f1_record(make_dict(STR, INT32), analyzer)
            assert not _f1_record(STR, analyzer)

    def test_a_builtin_container_ctor_keeps_its_own_emit(self):
        # `list[Int32]()` carries a formatter too, but a LIST category is not
        # the record branch: it must keep taking the instantiation render, not
        # the record-ctor one the Waker face now reaches.
        src = ("from tpy import Int32\n"
               "def f() -> Int32:\n"
               "    xs = list[Int32]()\n"
               "    xs.append(1)\n"
               "    return len(xs)\n")
        _assert_routes_byte_identical(src)
        _, witnessed = _lower_ctx_witnessed(src)
        assert witnessed.get("call.instantiation_empty", 0) > 0
        assert witnessed.get("ctor.cross_module", 0) == 0
        assert witnessed.get("ctor.call", 0) == 0
