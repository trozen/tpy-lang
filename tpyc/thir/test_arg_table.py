"""Pins for the arg-table machinery (tpyc/thir/lower/arg_table.py).

The corpus is the real witness for what the tables ADMIT; these pin the
machinery the fold rests on: that row order survives (reordering changes the
face census, so it is not cosmetic), that a shape name cannot reach two
different predicates, and that the WALK obeys its contract -- prologue ahead
of the rows, first admitting row wins, `extra` guarding before its predicate,
`decisive` rejecting at its own position, and the family note only on a
fall-through.
"""

import inspect
import itertools
import types

import pytest

from ..compilation_context import _current_compiler, activate_compiler
from ..parse.nodes import TpyIntLiteral, TpyName, TpyStrLiteral
from ..typesys import (CHAR, INT32, STRVIEW, ConcreteCoroType, NominalType,
                       OptionalType, OwnType, PtrType, RecursiveUnionInfo,
                       TupleType, TypeParamRef, UnionType)
from .lower import checks
from .lower.arg_table import (NO_CELL, PROLOGUE_CELL, _ArgReq, _ArgRow,
                              _ArgSink, arg_ok, reached, reached_families,
                              register_sink, registered_cells,
                              registered_families)
from .lower import expressions
from .lower.context import (SinkForm, SinkPos, _POS_FORMS, _ExprUse,
                            _RecordCtorUse, _call_arg_forms,
                            _decl_slot_forms)
from .lower.expressions import (_CTOR_ARG_SINK, _CTOR_NESTED_ARG_SINK,
                                _pre_ctor_nested_slot_family)
from .lower.checks import (_GENERIC_PLAIN_ARG_SINK,
                           _MARKER_NATIVE_ARG_SINK, _MARKER_OWN_ROWS,
                           _MARKER_OWN_SLOT_KINDS,
                           _MARKER_QUALIFIED_ARG_SINK, _MARKER_ROWS,
                           _MARKER_TEMPLATE_ARG_SINK, _NATIVE_ARG_SINK,
                           _PLAIN_ARG_SINK, _PROTOCOL_ARG_SINK,
                           _METHOD_ARG_SINK,
                           _pre_container_slot_family,
                           _pre_generic_slot_family)


def _fake_compiler():
    """Enough of a Compiler for the witness / note_detail sinks."""
    return types.SimpleNamespace(
        _thir_face_witnesses={}, _thir_face_journal={},
        _thir_reject_reason=None, _thir_reject_detail=None,
        _thir_arg_reached={},
        union_wrapper_index={}, union_alias_names={})


def _call(sink, verdict_src=None):
    return arg_ok(sink, verdict_src, None, {}, None,
                  param_names=frozenset(), narrowed=frozenset(),
                  temps_ok=False)


def _yes(req):
    return True


def _no(req):
    return False


class TestProtocolSinkShape:
    def test_row_order_is_pinned(self):
        assert [r.row for r in _PROTOCOL_ARG_SINK.rows] == [
            "shared_pass_through",
            "ru_wrapper_name",
            "optional_ptr",
            "value_record_rvalue",
            "tuple_literal",
            "wide_opt_deref_name",
            "func_ref",
            "lambda",
            "callable_value_pass",
            "str_owned_slot",
            "str_pass_through",
            "container_field_pass",
            "container_module_var",
            "record_field_marker",
            "record_elem_subscript",
            "container_literal_method",
            "none_unit",
            "none_value_opt",
            "value_opt_member",
            "union_pass_through",
            "union_member_lift",
            "union_coerced_literal",
            "protocol_slot",
        ]

    def test_note_tail_is_verbatim(self):
        assert _PROTOCOL_ARG_SINK.note == "method.protocol.arg_shape"

    def test_optional_ptr_is_the_only_witnessing_cell(self):
        # The face fires from the cell, after the predicate -- the pre-fold
        # ladder's `<pred> and _witness(...)` position.
        assert [(r.row, r.face) for r in _PROTOCOL_ARG_SINK.rows
                if r.face is not None] == [
            ("optional_ptr", "method.protocol_optional_ptr")]

    def test_the_only_own_slot_cell_is_the_str_convert(self):
        # An `Own[str]` protocol slot takes the view->owned convert, which
        # renders in place -- the one Own-slot shape that is temp-free, and
        # so the one this family can hold. The rest of the Own cascade
        # hoists and stays out.
        assert ({r.row for r in _PROTOCOL_ARG_SINK.rows} & _MARKER_OWN_ROWS
                == {"str_owned_slot"})
        assert _PROTOCOL_ARG_SINK.mutated_slots is False


class TestNativeSinkShape:
    def test_row_order_is_pinned(self):
        assert [r.row for r in _NATIVE_ARG_SINK.rows] == [
            "shared_pass_through",
            "func_ref",
            "lambda",
            "callable_value_pass",
            "own_move",
            "native_own_scalar_lvalue",
            "native_iterable_container",
            "container_field_pass",
            "inst_slice",
            "container_ternary",
            "native_iterable_call",
            "native_iterable_range",
            "native_iterable_iterator_call",
            "native_iterable_genexpr",
            "native_iterable_literal",
            "native_value_call",
            "native_container_call",
            "native_record_call",
            "protocol_slot",
            "native_protocol_value",
            "native_protocol_open_call",
            "native_optptr_name",
            "native_union_name",
            "native_protocol_tuple_literal",
            "native_protocol_field",
            "native_iterable_comp",
            "borrow_tuple_storage_name",
            "borrow_tuple_field",
            "open_value_tuple_name",
        ]

    def test_note_tail_is_the_slot_drilldown(self):
        # This family ranks its reject mass by SLOT shape instead of spelling
        # one constant tag, so its tail is a callable; probe_corpus.py
        # histograms on the strings it returns.
        assert callable(_NATIVE_ARG_SINK.note)
        req = _ArgReq(None, OptionalType(INT32), {}, None, frozenset(),
                      frozenset(), False, False)
        assert _NATIVE_ARG_SINK.note(req) == "call.native_arg.optptr"

    def test_witnessing_cells_are_pinned(self):
        assert [(r.row, r.face) for r in _NATIVE_ARG_SINK.rows
                if r.face is not None] == [
            ("inst_slice", "arg.native_slice_subscript"),
            ("open_value_tuple_name", "arg.open_value_tuple_name")]

    def test_family_carries_own_rows_and_no_mutated_policy(self):
        # Absence-preserving: the pre-fold ladder carried Own rows
        # (`own_move` / `native_own_scalar_lvalue`) and never consulted
        # `mutated_params`, so the Own cells are present and the mutated-slot
        # policy is off.
        rows = {r.row for r in _NATIVE_ARG_SINK.rows}
        assert "own_move" in rows and "native_own_scalar_lvalue" in rows
        assert _NATIVE_ARG_SINK.mutated_slots is False

    def test_storage_tuple_locals_reaches_the_borrow_tuple_row(self):
        # The one per-call input this family adds to the request. Without it
        # the row cannot tell a storage-form tuple name from a borrow-form
        # one, and the cell would silently admit the wrong half.
        seen = []

        def _spy(req):
            seen.append(req.storage_tuple_locals)
            return False

        sink = _ArgSink(family="t_stl", note="x.y",
                        rows=(_ArgRow("t_stl1", _spy),))
        assert arg_ok(sink, None, None, {}, None, param_names=frozenset(),
                      narrowed=frozenset(), temps_ok=False,
                      storage_tuple_locals=frozenset({"pairs"})) is False
        assert seen == [frozenset({"pairs"})]


class TestMethodArgSinkShape:
    """The ONE method-argument family: every receiver kind a method call can
    have -- the builtin stubs (containers, bytearray, the str/bytes views,
    the scalars, the ptr template) and user records alike.

    Two listings until the union: what decided an argument was already the
    resolved SLOT and the argument's shape, never which receiver kind led to
    the call, so the split could only close a shape to one receiver by
    omission. The rows are the stub listing followed by the rows only a user
    record's signature reaches; both halves only admit, so where a shape is
    in both, the leading (stub) cell decides it.
    """

    # Where the record-only rows start. Named once, so the split-point pins
    # below read as facts about the merge rather than magic offsets. One row
    # a stub can never reach sits in the leading half anyway: the hoisting
    # `container_literal` cell has to follow its inline sibling immediately,
    # or a row between the two would claim the literal first.
    STUB_ROWS = 52

    def test_row_order_is_pinned(self):
        # Order is load-bearing: `_witness` fires during the walk, so a
        # reordering changes the recorded face census even when admission is
        # identical. This pin is the tripwire for that. The STUB half leads
        # because that order is what measured render-identical over the whole
        # corpus -- record-first rejected 438 of 3850 compiling cases.
        assert [r.row for r in _METHOD_ARG_SINK.rows] == [
            # -- the builtin-stub rows --
            "scalar_at_template_slot",
            "protocol_bare_name",
            "copy_iter_own_elem",
            "str_pass_through",
            "str_owned_slot",
            "bytes_owned_literal",
            "bytes_view_literal",
            "bytes_owned_slot",
            "bytes_owned_lvalue",
            "bytes_owned_call_rvalue",
            "bytes_pass_through",
            "char_pass_through",
            "enum_pass_through",
            "own_enum_elem",
            "ptr_pass_through",
            "container_pass_through",
            "container_slot_call_rvalue",
            "own_record_rvalue",
            "copy_own",
            "own_move",
            "own_lvalue",
            "native_iterable_literal",
            "native_iterable_container",
            "native_iterable_field",
            "native_iterable_call",
            "native_iterable_comp",
            "own_iter_special",
            "own_container_literal",
            "own_container_comp",
            "any_pass_through",
            "container_literal_method",
            "container_literal",
            "container_comp",
            "none_value_opt",
            "opt_view_own_elem",
            "opt_view_param_own_elem",
            "opt_strview_to_str_own_elem",
            "value_opt_scalar_elem",
            "own_ptr_value",
            "none_unit",
            "callable_slot",
            "tparam_slot",
            "own_value_tuple_literal",
            "own_open_t_tuple_literal",
            "own_btuple_literal",
            "own_btuple_storage_source",
            "own_open_t_tuple_storage_source",
            "own_btuple_mixed_call",
            "own_btuple_nested_name",
            "own_btuple_borrow_name",
            "own_tuple_call_rvalue",
            "ptr_addr_of_elem",
            # -- the rows only a user record's signature reaches --
            "lambda",
            "func_ref",
            "callable_value_pass",
            "callable_object",
            "plain_scalar_slot",
            "tparam_scalar",
            "tparam_open_pass",
            "float_literal_pass_through",
            "int_literal_bigint",
            "value_tuple_pass_through",
            "nullable_proto_addr",
            "span_coerce",
            "slice_ctor_pass_through",
            "own_scalar_rvalue",
            "value_opt_pass_through",
            "value_opt_callable_pass",
            "value_opt_tuple_pass",
            "value_opt_view_whole",
            "value_record_rvalue",
            "value_opt_record_rvalue",
            "value_record_name",
            "value_array_call",
            "own_tparam_call_rvalue",
            "opt_own_record_name",
            "own_optional_record_rvalue",
            "own_opt_slot",
            "own_opt_ptr_name_move",
            "opt_own_ptr_opt_name_move",
            "union_member_lift_none",
            "optional_ptr_no_temp",
            "container_field_pass",
            "record_pass_through",
            "record_field_marker",
            "record_elem_subscript",
            "method_ctor_rvalue",
            "record_rvalue_temp_factory",
            "dyn_own_conformer",
            "tparam_slot_temp",
            "struct_proto_union",
            "tuple_literal",
            "method_value_union",
            "union_ctor_temp",
            "union_bytes_literal_temp",
            "union_pass_deep_const",
            "value_union_temp",
            "value_union_narrowed_pass",
            "value_opt_scalar_value",
            "str_literal_value_opt",
            "bytes_literal_value_opt",
            "optional_ptr_container_temp",
            "optional_ptr_scalar_temp",
            "protocol_slot",
            "ru_wrapper_name",
            "ru_wrapper_member_name",
            "ru_wrapper_scalar_literal",
            "ru_wrapper_member_rvalue",
            "borrow_ret_record_marker",
            "wide_opt_deref_name",
        ]

    def test_the_two_halves_share_fifteen_cells(self):
        # The union is the stub listing plus the record rows it did not
        # already name: 15 shapes were written twice, and the leading cell is
        # now the only one deciding them.
        rows = [r.row for r in _METHOD_ARG_SINK.rows]
        assert len(rows) == 110
        assert len(rows[:self.STUB_ROWS]) == 52
        assert rows[self.STUB_ROWS] == "lambda"

    def test_str_owned_slot_precedes_own_lvalue(self):
        # Load-bearing beyond the face census: `str_owned_slot` absorbs the
        # VIEW-form str sources into the inline convert, which is what leaves
        # `Own[str]` as the only position-sensitive payload reaching the
        # `own_lvalue` cell at a STUB slot.
        rows = [r.row for r in _METHOD_ARG_SINK.rows]
        assert rows.index("str_owned_slot") < rows.index("own_lvalue")
        # ... and the temp-free MOVE half is decided before it too.
        assert rows.index("own_move") < rows.index("own_lvalue")

    def test_the_view_rows_keep_their_relative_order(self):
        # The stub half is the CONTAINER ladder's order with the one
        # view-only row spliced in at its view-relative position, which works
        # only because the view ladder's order was already a subsequence of
        # the container one. Interleaving the other way round (view rows
        # first) measured identical on every corpus, but it would put
        # `bytes_pass_through` ahead of the owned-bytes cells, inverting a
        # precedence the container ladder states -- so the subsequence is the
        # property, not the coincidence.
        rows = [r.row for r in _METHOD_ARG_SINK.rows]
        view = ["scalar_at_template_slot", "protocol_bare_name",
                "str_pass_through", "bytes_pass_through", "char_pass_through",
                "enum_pass_through", "ptr_pass_through",
                "native_iterable_literal", "native_iterable_container",
                "native_iterable_field", "native_iterable_call",
                "native_iterable_comp"]
        assert [r for r in rows if r in view] == view

    def test_note_tail_is_verbatim(self):
        # probe_sites.py / probe_corpus.py histogram on this string, and both
        # halves already rejected with it -- the AST method loop spelt one
        # tag for every receiver kind, which is the fact the union states.
        assert _METHOD_ARG_SINK.note == "method.arg_shape"

    def test_witnessing_cells_are_pinned(self):
        assert [(r.row, r.face) for r in _METHOD_ARG_SINK.rows
                if r.face is not None] == [
            ("bytes_owned_literal", "arg.bytes_owned_literal"),
            ("bytes_view_literal", "arg.bytes_view_literal"),
            ("own_enum_elem", "arg.own_enum_elem"),
            ("container_slot_call_rvalue", "arg.container_call_rvalue"),
            ("own_ptr_value", "arg.own_ptr_value"),
            ("own_value_tuple_literal", "arg.own_value_tuple_literal"),
            ("own_open_t_tuple_literal",
             "arg.own_open_t_tuple_literal"),
            ("own_btuple_literal", "arg.own_btuple_literal"),
            ("own_open_t_tuple_storage_source",
             "arg.own_open_t_tuple_storage_source"),
            ("own_btuple_borrow_name", "arg.own_btuple_borrow_name"),
            ("tparam_scalar", "method.tparam_scalar_arg"),
            ("tparam_open_pass", "method.tparam_open_pass_arg"),
            ("nullable_proto_addr", "arg.nullable_proto_addr"),
            ("union_pass_deep_const", "method.union_pass_arg"),
            ("bytes_literal_value_opt", "method.bytes_literal_value_opt"),
        ]

    def test_the_comprehension_cell_is_one_cell_now(self):
        # The record half carried a SECOND comprehension cell
        # (`comp_container_method`, face `arg.comprehension_method`) whose
        # only difference from `container_comp` was the const-slot verdict.
        # That verdict is the shared cell's guard now, so the twin is gone
        # rather than sitting unreachable behind it.
        rows = [r.row for r in _METHOD_ARG_SINK.rows]
        assert "comp_container_method" not in rows
        assert rows.count("container_comp") == 1

    def test_family_carries_own_rows_and_no_mutated_policy(self):
        assert ({r.row for r in _METHOD_ARG_SINK.rows} & _MARKER_OWN_ROWS)
        # Absence-preserving: the record half has `overload.mutated_params`
        # in hand and never consults it, so the flag stays off. Turning it on
        # moves which bodies route, so it is its own change.
        assert _METHOD_ARG_SINK.mutated_slots is False

    def test_own_lvalue_gates_on_the_flush_or_the_stub_slot(self):
        # The one cell whose guard the two halves spelt DIFFERENTLY, because
        # their `Own[T]` slots are different C++: a record method's is a
        # by-value `T&&` param that needs the copy temp (and so a flush
        # position), a stub's is a container insert whose const-ref overload
        # takes the lvalue with no temp at all. Dropping either half of this
        # disjunction is a miscompile or a lost admission, both probed:
        # `s.take(b)` inside a call arg emitted an lvalue into a `Point&&`
        # slot; `xs.append(b)` stopped compiling.
        cell = next(r for r in _METHOD_ARG_SINK.rows
                    if r.row == "own_lvalue")

        def _req(temps_ok, overload):
            return _ArgReq(None, None, {}, None, frozenset(), frozenset(),
                           False, temps_ok, index=0, overload=overload)

        assert cell.extra(_req(False, None)) is True     # stub slot
        assert cell.extra(_req(True, "FI")) is True      # record + flush
        assert cell.extra(_req(False, "FI")) is False    # record, no flush

    def test_container_comp_gates_on_the_const_slot(self):
        # The comprehension's stmt-expr is a PRVALUE, so the slot must be a
        # const borrow. A stub carries no signature-const facts and every
        # container slot it spells is a `const T&`; a record overload states
        # the verdict per position, which is what keeps
        # `calls/error_method_comprehension_mutated` rejecting.
        cell = next(r for r in _METHOD_ARG_SINK.rows
                    if r.row == "container_comp")

        def _req(overload, index=0):
            return _ArgReq(None, None, {}, None, frozenset(), frozenset(),
                           False, False, index=index, overload=overload)

        fi = types.SimpleNamespace(const_borrow_params=frozenset({0}))
        assert cell.extra(_req(None)) is True
        assert cell.extra(_req(fi)) is True
        assert cell.extra(_req(fi, index=1)) is False
        assert cell.extra(
            _req(types.SimpleNamespace(const_borrow_params=None))) is False

    def test_the_inline_literal_cell_declines_a_lending_callee(self):
        # The second half of that cell's guard: a literal bound IN PLACE dies
        # at the end of the full expression, so a callee whose return borrows
        # THIS parameter may not have it -- and a callee whose facts were
        # never computed declines too, since "not measured" is not evidence
        # of an owning result.
        cell = next(r for r in _METHOD_ARG_SINK.rows
                    if r.row == "container_literal_method")

        def _fi(borrows):
            root = types.SimpleNamespace(const_borrow_params=frozenset({0, 1}),
                                         return_borrows_from=borrows)
            root.root = root
            return root

        def _req(overload, index=0):
            return _ArgReq(None, None, {}, None, frozenset(), frozenset(),
                           False, False, index=index, overload=overload)

        assert cell.extra(_req(None)) is True                    # stub slot
        assert cell.extra(_req(_fi(frozenset()))) is True         # owns it
        assert cell.extra(_req(_fi(frozenset({1})))) is True      # other arg
        assert cell.extra(_req(_fi(frozenset({0})))) is False     # lends it
        assert cell.extra(_req(_fi(None))) is False               # unknown

    def test_guarded_cells_are_pinned(self):
        # Eleven of these carry `_x_insert_own_slot`: their `Own[T]` slot
        # premise is the builtin INSERT's C++, which a user signature's
        # by-value `Own[T]` param does not have.
        assert [(r.row, r.extra.__name__) for r in _METHOD_ARG_SINK.rows
                if r.extra is not None] == [
            ("bytes_owned_lvalue", "_x_insert_own_slot"),
            ("own_enum_elem", "_x_insert_own_slot"),
            ("container_slot_call_rvalue", "_x_comp_slot_const"),
            ("own_lvalue", "_x_own_lvalue_flush"),
            ("container_literal_method", "_x_inline_container_literal"),
            ("container_literal", "_x_temps_ok"),
            ("container_comp", "_x_comp_slot_const"),
            ("opt_view_own_elem", "_x_insert_own_slot"),
            ("opt_view_param_own_elem", "_x_insert_own_slot"),
            ("opt_strview_to_str_own_elem", "_x_insert_own_slot"),
            ("value_opt_scalar_elem", "_x_insert_own_slot"),
            ("own_ptr_value", "_x_insert_own_slot"),
            ("tparam_slot", "_x_insert_own_slot"),
            ("own_btuple_storage_source", "_x_insert_own_slot"),
            ("own_open_t_tuple_storage_source", "_x_insert_own_slot"),
            ("own_btuple_nested_name", "_x_insert_own_slot"),
            ("own_btuple_borrow_name", "_x_insert_own_slot"),
            ("record_rvalue_temp_factory", "_x_temps_and_frame"),
            ("tparam_slot_temp", "_x_temps_ok"),
            ("union_ctor_temp", "_x_temps_ok"),
            ("union_bytes_literal_temp", "_x_temps_ok"),
            ("value_union_temp", "_x_temps_ok"),
            ("optional_ptr_container_temp", "_x_temps_ok"),
            ("optional_ptr_scalar_temp", "_x_temps_ok"),
            ("ru_wrapper_member_name", "_x_temps_ok"),
            ("ru_wrapper_scalar_literal", "_x_temps_ok"),
            ("ru_wrapper_member_rvalue", "_x_temps_ok"),
        ]

    def test_the_insert_slot_guard_is_the_stub_half(self):
        # The fact it states: a stub's `Own[T]` slot is a container insert
        # whose const-ref overload binds an lvalue and copies; a record
        # method's is `own_param_t<T>` (`T&&`), which no lvalue binds. Probed
        # both ways -- an element read at an open `Own[T]` method slot
        # emitted `push(::tpy::__getitem__(src, i))` without it.
        cell = next(r for r in _METHOD_ARG_SINK.rows
                    if r.row == "tparam_slot")

        def _req(overload):
            return _ArgReq(None, None, {}, None, frozenset(), frozenset(),
                           False, False, index=0, overload=overload)

        assert cell.extra(_req(None)) is True
        assert cell.extra(_req("FI")) is False

    def test_the_factory_cell_gates_on_both_facts(self):
        # `temps_ok and frame_capturing and` -- the one cell in the fold
        # whose pre-guard is a conjunction, and the only reader of
        # `frame_capturing`. Both halves must be required.
        cell = next(r for r in _METHOD_ARG_SINK.rows
                    if r.row == "record_rvalue_temp_factory")

        def _req(temps_ok, frame):
            return _ArgReq(None, None, {}, None, frozenset(), frozenset(),
                           False, temps_ok, index=0,
                           frame_capturing=frame)

        assert cell.extra(_req(True, True)) is True
        assert cell.extra(_req(True, False)) is False
        assert cell.extra(_req(False, True)) is False

    def test_the_shared_rows_reach_the_other_families_cells(self):
        # `register_sink` has already proved each shared name reaches the
        # IDENTICAL predicate (it fails the import otherwise). This pins how
        # much of the widest family is written for someone else too.
        others = set()
        for sink in (_PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _MARKER_QUALIFIED_ARG_SINK,
                     _PLAIN_ARG_SINK, _GENERIC_PLAIN_ARG_SINK):
            others |= {r.row for r in sink.rows}
        rows = [r.row for r in _METHOD_ARG_SINK.rows]
        assert len([r for r in rows if r in others]) == 50
        assert [r for r in rows if r not in others] == [
            "scalar_at_template_slot",
            "protocol_bare_name",
            "copy_iter_own_elem",
            "bytes_owned_literal",
            "bytes_view_literal",
            "bytes_owned_lvalue",
            "bytes_pass_through",
            "char_pass_through",
            "enum_pass_through",
            "own_enum_elem",
            "ptr_pass_through",
            "container_pass_through",
            "container_slot_call_rvalue",
            "native_iterable_field",
            "own_iter_special",
            "any_pass_through",
            "opt_view_own_elem",
            "opt_view_param_own_elem",
            "opt_strview_to_str_own_elem",
            "value_opt_scalar_elem",
            "own_ptr_value",
            "callable_slot",
            "tparam_slot",
            "own_value_tuple_literal",
            "own_open_t_tuple_literal",
            "own_btuple_literal",
            "own_btuple_storage_source",
            "own_open_t_tuple_storage_source",
            "own_btuple_mixed_call",
            "own_btuple_nested_name",
            "own_btuple_borrow_name",
            "ptr_addr_of_elem",
            "plain_scalar_slot",
            "tparam_scalar",
            "tparam_open_pass",
            "float_literal_pass_through",
            "int_literal_bigint",
            "value_tuple_pass_through",
            "span_coerce",
            "slice_ctor_pass_through",
            "own_scalar_rvalue",
            "value_opt_view_whole",
            "value_opt_record_rvalue",
            "value_record_name",
            "union_member_lift_none",
            "optional_ptr_no_temp",
            "record_pass_through",
            "method_ctor_rvalue",
            "record_rvalue_temp_factory",
            "tparam_slot_temp",
            "struct_proto_union",
            "method_value_union",
            "union_ctor_temp",
            "union_bytes_literal_temp",
            "union_pass_deep_const",
            "value_opt_scalar_value",
            "str_literal_value_opt",
            "bytes_literal_value_opt",
            "optional_ptr_container_temp",
            "optional_ptr_scalar_temp",
        ]

    def test_the_lookalike_cells_are_not_the_shared_ones(self):
        # Five names deliberately shadow a shared row because the predicate
        # is DIFFERENT, and `register_sink` would have rejected reusing the
        # shared name. Pinned so a later step cannot quietly collapse them:
        # collapsing any pair is a behaviour change, not a rename.
        rows = {r.row: r.fn for r in _METHOD_ARG_SINK.rows}
        shared = {r.row: r.fn for r in _PLAIN_ARG_SINK.rows}
        for mine, theirs in (
                # bare vs coerce-peeling.
                ("str_literal_value_opt", "str_literal_value_opt_coerced"),
                # temps_ok=False hardcoded vs threaded.
                ("optional_ptr_no_temp", "optional_ptr"),
                # TpyNoneLiteral-only vs the full member set.
                ("union_member_lift_none", "union_member_lift"),
                # frame_capturing=True vs upcast_ok=True.
                ("record_rvalue_temp_factory", "record_rvalue_temp"),
                # ... and the deep-const-borrow restriction.
                ("union_pass_deep_const", "union_pass_through")):
            assert rows[mine] is not shared[theirs], (mine, theirs)

    def test_the_plain_cells_absent_here_are_named(self):
        # This family is NOT a subset of plain, and the cells of plain's it
        # still lacks are COUNTED and named, so filling one is a visible
        # edit here rather than a silent widening.
        plain = {r.row for r in _PLAIN_ARG_SINK.rows}
        mine = {r.row for r in _METHOD_ARG_SINK.rows}
        assert len(plain - mine) == 38
        assert {"borrow_tuple_field", "borrow_tuple_subscript",
                "callable_field", "container_module_var",
                "covariant_temp", "deref_coerce", "dyn_own_coro_factory",
                "dyn_own_forward_call", "dyn_own_handle", "list_repeat_proto",
                "opt_own_record_rvalue", "opt_string_literal",
                "opt_view_identity_coerce", "optional_ptr", "own_coerce_cast",
                "own_tuple_storage_elem", "own_union_call_pass",
                "own_union_ctor", "readonly_container_rvalue",
                "readonly_record_ctor", "record_borrow_call",
                "record_field_ref",
                "record_rvalue_temp", "recursive_union_borrow_call",
                "ref_param_dictset_literal", "required_protocol_union",
                "ru_container_literal", "ru_wrapper_borrow_call",
                "ru_wrapper_field", "ru_wrapper_own_call",
                "ru_wrapper_value_call", "shared_pass_through",
                "str_literal_value_opt_coerced", "tuple_literal_value_opt",
                "union_coerced_literal", "union_member_lift",
                "union_pass_through",
                "wrapper_ref_tuple_elem"} <= (plain - mine)

    def test_it_does_not_open_with_the_shared_pass_through_fold(self):
        # Neither half spelt that fold: both list its members out one by one,
        # in their own order and without four of them -- so the cells are
        # separate here and `shared_pass_through` is genuinely absent, not
        # renamed.
        mine = {r.row for r in _METHOD_ARG_SINK.rows}
        assert "shared_pass_through" not in mine
        assert {"str_pass_through", "bytes_pass_through", "char_pass_through",
                "enum_pass_through", "ptr_pass_through",
                "value_tuple_pass_through", "span_coerce",
                "slice_ctor_pass_through", "own_scalar_rvalue",
                "own_record_rvalue", "container_pass_through",
                "record_pass_through", "float_literal_pass_through",
                "int_literal_bigint"} <= mine


class TestMarkerSinkSplit:
    """The marker call is THREE families, not one -- the step that turns
    `own_ok` from a re-typed row prefix into a family capability."""

    def test_qualified_row_order_is_pinned(self):
        assert [r.row for r in _MARKER_QUALIFIED_ARG_SINK.rows] == [
            "shared_pass_through",
            "func_ref",
            "lambda",
            "value_opt_callable_pass",
            "value_opt_pass_through",
            "value_opt_tuple_pass",
            "none_unit",
            "value_union_temp",
            "value_union_narrowed_pass",
            "own_record_rvalue",
            "own_tparam_call_rvalue",
            "copy_own",
            "str_owned_slot",
            "own_move",
            "own_lvalue",
            "optional_ptr",
            "opt_own_record_name",
            "own_opt_ptr_name_move",
            "opt_own_ptr_opt_name_move",
            "readonly_record_ctor",
            "union_pass_through",
            "union_member_lift",
            "union_coerced_literal",
            "value_opt_member",
            "value_opt_field_pass",
            "none_value_opt",
            "ru_container_literal",
            "ru_wrapper_name",
            "ru_wrapper_field",
            "own_union_ctor",
            "dyn_own_coro_factory",
            "dyn_own_handle",
            "dyn_own_forward_call",
            "container_literal_method",
            "container_literal",
            "container_field_pass",
            "record_field_marker",
            "record_elem_subscript",
            "value_tuple_field_pass",
            "borrow_ret_record_marker",
            "btuple_literal_marker",
            "same_tparam_name",
            "own_container_literal",
            "own_container_comp",
            "native_record_call",
            "protocol_slot",
            "wide_opt_deref_name",
            "container_comp",
        ]

    def test_template_family_carries_one_row(self):
        # The template callee expands over the builtins arg loop, whose
        # only mirrored render is the shared pass-through set -- the
        # ladder's early `return` before its own chain.
        assert [r.row for r in _MARKER_TEMPLATE_ARG_SINK.rows] == [
            "shared_pass_through"]

    def test_native_family_is_the_qualified_tuple_minus_the_own_cells(self):
        # The split is a FILTER of one listing, never a re-typing: every
        # surviving row keeps its position, so the two families interleave
        # exactly as the single ladder did (the face census is
        # order-sensitive).
        assert [r.row for r in _MARKER_NATIVE_ARG_SINK.rows] == [
            r.row for r in _MARKER_QUALIFIED_ARG_SINK.rows
            if r.row not in _MARKER_OWN_ROWS]

    def test_the_three_families_cover_exactly_the_old_row_set(self):
        # Absence-preserving: splitting one ladder into three sinks must not
        # change which rows a call can see. `_MARKER_ROWS` IS the pre-fold
        # ladder's row set, and the template family's single row is a
        # member of it.
        union = set()
        for sink in (_MARKER_TEMPLATE_ARG_SINK, _MARKER_NATIVE_ARG_SINK,
                     _MARKER_QUALIFIED_ARG_SINK):
            union |= {r.row for r in sink.rows}
        assert union == {r.row for r in _MARKER_ROWS}

    def test_own_cells_are_the_ones_the_ladder_prefixed(self):
        assert _MARKER_OWN_ROWS == frozenset({
            "own_record_rvalue", "own_tparam_call_rvalue", "copy_own",
            "own_move", "own_lvalue", "own_union_ctor", "str_owned_slot",
            "dyn_own_coro_factory", "dyn_own_handle", "dyn_own_forward_call",
            "own_container_literal", "own_container_comp",
            "own_opt_ptr_name_move", "opt_own_ptr_opt_name_move"})

    def test_own_capability_is_row_membership_not_a_flag(self):
        # The Own capability IS which cells the family holds -- there is no
        # second declaration of it that could drift from the tuple.
        assert not ({r.row for r in _MARKER_TEMPLATE_ARG_SINK.rows}
                    & _MARKER_OWN_ROWS)
        assert not ({r.row for r in _MARKER_NATIVE_ARG_SINK.rows}
                    & _MARKER_OWN_ROWS)
        assert ({r.row for r in _MARKER_QUALIFIED_ARG_SINK.rows}
                & _MARKER_OWN_ROWS) == _MARKER_OWN_ROWS
        # ... and no family declines a temp source at a mutated `T&` slot:
        # only the ctor ladder holds that rule, and extending it moves which
        # bodies route, so the fold transcribes the absence.
        for sink in (_MARKER_TEMPLATE_ARG_SINK, _MARKER_NATIVE_ARG_SINK,
                     _MARKER_QUALIFIED_ARG_SINK):
            assert sink.mutated_slots is False

    def test_note_tails_are_verbatim(self):
        # probe_sites.py / probe_corpus.py histogram on these strings.
        req = _ArgReq(None, OptionalType(INT32), {}, None, frozenset(),
                      frozenset(), False, False)
        assert (_MARKER_TEMPLATE_ARG_SINK.note(req)
                == "call.native_arg.optptr")
        assert (_MARKER_NATIVE_ARG_SINK.note(req)
                == "method.qualcall.arg.optional")
        assert (_MARKER_QUALIFIED_ARG_SINK.note(req)
                == "method.qualcall.arg.optional")

    def test_witnessing_cell_is_pinned(self):
        assert [(r.row, r.face) for r in _MARKER_QUALIFIED_ARG_SINK.rows
                if r.face is not None] == [
            ("same_tparam_name", "arg.same_tparam_name")]

    def test_flush_gated_cells_are_pinned(self):
        # The `temps_ok and` prefixes the ladder spelled, as PRE-guards.
        # `own_lvalue` carries one HERE where the container family does not
        # -- an asymmetry the pre-fold ladders already had, transcribed on
        # both sides rather than reconciled (adding the missing guard is a
        # no-op, so tidying it would only move a recorded tag).
        assert [r.row for r in _MARKER_QUALIFIED_ARG_SINK.rows
                if r.extra is not None] == [
            "value_union_temp", "own_lvalue", "ru_container_literal",
            "container_literal_method", "container_literal", "container_comp"]

    def test_no_cell_carries_an_own_slots_pre_guard(self):
        # The point of the split: `own_ok` selects which sink a call reaches,
        # never a guard re-typed on nine cells. If this fails, the fold
        # transcribed the prefix instead of removing it.
        for sink in (_MARKER_NATIVE_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK):
            for row in sink.rows:
                if row.extra is None:
                    continue
                assert "own_slots" not in inspect.getsource(row.extra)

    def test_the_kind_selects_the_sink(self, monkeypatch):
        # The dispatcher itself. Row-shape pins prove WHAT each sink admits
        # and never that a callee reaches its own -- a swapped branch would
        # hand a whole family the wrong ladder and only show up if the
        # corpus happened to carry all three kinds.
        seen = []

        def _spy(sink, *args, **kwargs):
            seen.append(sink)
            return True

        monkeypatch.setattr(checks, "arg_ok", _spy)
        # The final kind is unlisted: the else-leg is a catch-all, so a new
        # marker kind lands on the native sink until it is named.
        kinds = ("template", *_MARKER_OWN_SLOT_KINDS, "native", "unlisted")
        for kind in kinds:
            checks._marker_call_arg_ok(None, None, (kind, "f"), {}, None,
                                       temps_ok=False, narrowed=frozenset())
        assert seen == [_MARKER_TEMPLATE_ARG_SINK,
                        *(_MARKER_QUALIFIED_ARG_SINK
                          for _ in _MARKER_OWN_SLOT_KINDS),
                        _MARKER_NATIVE_ARG_SINK, _MARKER_NATIVE_ARG_SINK]
        assert _MARKER_OWN_SLOT_KINDS == (
            "qualified", "generic_qualified", "generic_static",
            "generic_module_static", "super_generic")


class TestPlainSinkShape:
    """The reference ladder every other family was copied from -- so its
    shared-row count is the fold's payoff, and `register_sink` proving those
    names reach the SAME predicate is the drift check."""

    def test_row_order_is_pinned(self):
        assert [r.row for r in _PLAIN_ARG_SINK.rows] == [
            "lambda",
            "func_ref",
            "callable_value_pass",
            "callable_field",
            "callable_object",
            "shared_pass_through",
            "container_field_pass",
            "container_module_var",
            "value_union_temp",
            "value_union_narrowed_pass",
            "record_rvalue_temp",
            "str_owned_slot",
            "bytes_owned_slot",
            "own_move",
            "own_coerce_cast",
            "own_lvalue",
            "container_literal",
            "ref_param_dictset_literal",
            "own_container_literal",
            "own_container_comp",
            "covariant_temp",
            "optional_ptr",
            "opt_own_record_name",
            "readonly_record_ctor",
            "union_pass_through",
            "required_protocol_union",
            "union_member_lift",
            "union_coerced_literal",
            "ru_wrapper_name",
            "ru_wrapper_borrow_call",
            "ru_wrapper_value_call",
            "ru_wrapper_field",
            "ru_wrapper_member_name",
            "ru_wrapper_scalar_literal",
            "ru_wrapper_member_rvalue",
            "ru_wrapper_own_call",
            "ru_container_literal",
            "own_union_ctor",
            "own_union_call_pass",
            "dyn_own_coro_factory",
            "dyn_own_handle",
            "dyn_own_conformer",
            "dyn_own_forward_call",
            "wide_opt_deref_name",
            "none_unit",
            "none_value_opt",
            "value_opt_pass_through",
            "opt_view_identity_coerce",
            "value_opt_callable_pass",
            "value_opt_tuple_pass",
            "protocol_slot",
            "list_repeat_proto",
            "nullable_proto_addr",
            "tuple_literal",
            "tuple_literal_value_opt",
            "own_tuple_storage_elem",
            "wrapper_ref_tuple_elem",
            "record_borrow_call",
            "own_optional_record_rvalue",
            "opt_own_record_rvalue",
            "str_literal_value_opt_coerced",
            "opt_string_literal",
            "own_opt_slot",
            "own_opt_ptr_name_move",
            "opt_own_ptr_opt_name_move",
            "value_array_call",
            "recursive_union_borrow_call",
            "record_elem_subscript",
            "container_comp",
            "borrow_tuple_field",
            "borrow_tuple_subscript",
            "record_field_ref",
            "deref_coerce",
            "readonly_container_rvalue",
        ]

    def test_note_tail_is_the_slot_drilldown(self):
        assert callable(_PLAIN_ARG_SINK.note)
        req = _ArgReq(None, OptionalType(INT32), {}, None, frozenset(),
                      frozenset(), False, False)
        assert _PLAIN_ARG_SINK.note(req) == "call.arg_shape.optional"

    def test_witnessing_cells_are_pinned(self):
        assert [(r.row, r.face) for r in _PLAIN_ARG_SINK.rows
                if r.face is not None] == [
            ("callable_field", "call.callable_field_arg"),
            ("opt_view_identity_coerce", "arg.optview_identity_coerce"),
            ("list_repeat_proto", "argtemp.list_repeat_proto"),
            ("nullable_proto_addr", "arg.nullable_proto_addr"),
            ("tuple_literal_value_opt", "arg.tuple_literal_value_opt"),
            ("opt_own_record_rvalue", "arg.opt_own_record_rvalue"),
            ("opt_string_literal", "arg.opt_string_literal"),
        ]

    def test_flush_gated_cells_are_pinned(self):
        # The `temps_ok and` prefixes the ladder spelled, as PRE-guards.
        assert [r.row for r in _PLAIN_ARG_SINK.rows
                if r.extra is not None] == [
            "value_union_temp",
            "record_rvalue_temp",
            "own_lvalue",
            "container_literal",
            "ref_param_dictset_literal",
            "covariant_temp",
            "ru_wrapper_member_name",
            "ru_wrapper_scalar_literal",
            "ru_wrapper_member_rvalue",
            "ru_wrapper_own_call",
            "ru_container_literal",
            # ... the one cell carrying a flush pre-guard AND a face.
            "list_repeat_proto",
            "container_comp",
        ]

    def test_deref_coerce_reads_temps_ok_inside_the_row(self):
        # Its flush test consults the PREDICATE'S verdict (inline vs the
        # `__deref__()` copy temp), so it cannot be a pre-guard -- the row
        # owns the whole conjunct rather than `extra` growing a post half.
        row = next(r for r in _PLAIN_ARG_SINK.rows if r.row == "deref_coerce")
        assert row.extra is None
        assert "req.temps_ok" in inspect.getsource(row.fn)

    def test_family_carries_own_rows_and_no_mutated_policy(self):
        # Absence-preserving: the pre-fold ladder carried the Own cells and
        # never consulted `mutated_params` (only the ctor ladder does, and
        # extending that is its own post-fold commit). Two of the three it
        # does NOT carry are a real HOLE: this ladder spells the record
        # rvalue as the flush-gated `record_rvalue_temp` instead. The third
        # is structural -- an OPEN `Own[T]` slot needs a generic callee, and
        # the generic family settles that slot in its prologue.
        rows = {r.row for r in _PLAIN_ARG_SINK.rows}
        assert (_MARKER_OWN_ROWS - rows
                == {"own_record_rvalue", "copy_own",
                    "own_tparam_call_rvalue"})
        assert "record_rvalue_temp" in rows
        assert _PLAIN_ARG_SINK.mutated_slots is False

    def test_the_shared_rows_reach_the_other_families_cells(self):
        # The fold's payoff, stated as a number: these row names are already
        # carried by an earlier-migrated family, and `register_sink` has
        # already proved each reaches the IDENTICAL predicate (it fails the
        # import otherwise). A drop here means a cell stopped being shared.
        others = set()
        for sink in (_PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _METHOD_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK):
            others |= {r.row for r in sink.rows}
        shared = [r.row for r in _PLAIN_ARG_SINK.rows if r.row in others]
        assert len(shared) == 50
        assert "lambda" in shared and "own_lvalue" in shared

    def test_the_coerce_peel_keeps_its_own_row_name(self):
        # This ladder peels the coerce where the method/ctor ladders pass the
        # argument bare. Transcribed, not reconciled -- so the cell gets a
        # distinct row name and cannot be mistaken for the bare shape once
        # the other families land theirs.
        assert ("str_literal_value_opt_coerced"
                in {r.row for r in _PLAIN_ARG_SINK.rows})
        assert ("str_literal_value_opt"
                not in {r.row for r in _PLAIN_ARG_SINK.rows})

    def test_self_capturable_reaches_the_lambda_row(self):
        # The one per-call input this family adds. It defaults False, which
        # is what every other family spelled, so the lambda cell stays
        # shared instead of forking into a plain-only copy.
        seen = []

        def _spy(req):
            seen.append(req.self_capturable)
            return False

        sink = _ArgSink(family="t_self", note="x.y",
                        rows=(_ArgRow("t_self1", _spy),))
        assert arg_ok(sink, None, None, {}, None, param_names=frozenset(),
                      narrowed=frozenset(), temps_ok=False,
                      self_capturable=True) is False
        assert arg_ok(sink, None, None, {}, None, param_names=frozenset(),
                      narrowed=frozenset(), temps_ok=False) is False
        assert seen == [True, False]



class TestGenericPlainSinkShape:
    """The generic (type-param-substituted) free-call family.

    Its cells decide against the SUBSTITUTED slot, which is what the family
    hands `arg_ok`; the unsubstituted one rides along as `open_ptype` for the
    prologue and the borrow-tuple cell. The design predicted "plain minus a
    few cells, plus a few" -- it is not: the order differs from the first row
    on, and 47 of plain's 68 cells are absent. Pinned as transcribed.
    """

    def test_row_order_is_pinned(self):
        assert [r.row for r in _GENERIC_PLAIN_ARG_SINK.rows] == [
            "generic_btuple_name",
            "generic_own_btuple_literal",
            "shared_pass_through",
            "lambda",
            "func_ref",
            "callable_value_pass",
            "callable_object",
            "own_move",
            "optional_ptr",
            "own_proto_container_slot",
            "own_lvalue",
            "opt_own_record_name",
            "dyn_own_coro_factory",
            "dyn_own_conformer",
            "dyn_own_forward_call",
            "none_unit",
            "none_value_opt",
            "tuple_literal",
            "own_str_literal",
            "bytes_owned_call_rvalue",
            "protocol_slot",
            "ru_wrapper_name",
            "ru_wrapper_borrow_call",
            "ru_wrapper_field",
            "ru_wrapper_member_name",
            "ru_wrapper_scalar_literal",
            "ru_wrapper_member_rvalue",
            "generic_list_literal",
            "generic_own_list_literal",
            "own_tuple_call_rvalue",
            "wide_opt_deref_name",
            "container_field_pass",
            "container_module_var",
            "record_field_ref",
            "record_elem_subscript",
            "container_comp",
            "record_rvalue_temp",
            "str_owned_slot",
            "bytes_owned_slot",
        ]

    def test_note_tail_is_verbatim(self):
        # The TAIL ladder's tag. The prologue's own rejects spell
        # `call.generic_arg_slot` / `call.generic_arg_shape` inline, so they
        # are not this constant and must not be folded into it.
        assert _GENERIC_PLAIN_ARG_SINK.note == "call.generic_arg_shape"

    def test_witnessing_cells_are_pinned(self):
        assert [(r.row, r.face) for r in _GENERIC_PLAIN_ARG_SINK.rows
                if r.face is not None] == [
            ("generic_btuple_name", "call.generic_btuple_name"),
            ("generic_own_list_literal", "call.generic_own_list_literal"),
        ]

    def test_flush_gated_cells_are_pinned(self):
        assert [r.row for r in _GENERIC_PLAIN_ARG_SINK.rows
                if r.extra is not None] == [
            "own_lvalue",
            "ru_wrapper_member_name",
            "ru_wrapper_scalar_literal",
            "ru_wrapper_member_rvalue",
            "generic_list_literal",
            "container_comp",
            "record_rvalue_temp",
        ]

    def test_it_is_not_plain_reordered(self):
        # The design's prediction, checked rather than assumed. Both halves
        # matter: the shared cells sit in a DIFFERENT order (row order is the
        # face census, so this is not cosmetic), and most of plain's ladder
        # is simply absent here.
        plain = [r.row for r in _PLAIN_ARG_SINK.rows]
        generic = [r.row for r in _GENERIC_PLAIN_ARG_SINK.rows]
        shared = set(plain) & set(generic)
        assert [r for r in plain if r in shared] != [
            r for r in generic if r in shared]
        # ... and the absences, COUNTED and named so a later step cannot
        # quietly fill one and call it a transcription. The count is what
        # closes the gap: naming a subset leaves the unnamed absences free
        # to be filled silently.
        assert len(set(plain) - set(generic)) == 43
        assert {"callable_field", "value_union_temp", "own_coerce_cast",
                "container_literal", "covariant_temp", "union_pass_through",
                "value_opt_pass_through",
                "deref_coerce"} <= (set(plain) - set(generic))

    def test_the_shared_rows_reach_the_other_families_cells(self):
        # `register_sink` has already proved each of these reaches the
        # IDENTICAL predicate (it fails the import otherwise). The six that
        # are NOT shared are the generic-only spellings: the substituted
        # borrow-tuple pair, the Own[protocol] container-conformer cell, the
        # literal-only Own[str] cell (plain's `str_owned_slot` also takes
        # view-form names), and the peeled list literals (plain's
        # `container_literal` does not peel).
        others = set()
        for sink in (_PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _METHOD_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK,
                     _PLAIN_ARG_SINK):
            others |= {r.row for r in sink.rows}
        rows = [r.row for r in _GENERIC_PLAIN_ARG_SINK.rows]
        assert [r for r in rows if r not in others] == [
            "generic_btuple_name",
            "generic_own_btuple_literal",
            "own_proto_container_slot",
            "own_str_literal",
            "generic_list_literal",
            "generic_own_list_literal",
        ]
        assert len([r for r in rows if r in others]) == 33

    def test_family_carries_no_mutated_policy(self):
        # Absence-preserving: the pre-fold ladder never consulted
        # `mutated_params`, so the flag stays off here too. Turning it on
        # moves which bodies route, so it is its own change.
        assert _GENERIC_PLAIN_ARG_SINK.mutated_slots is False


class TestGenericPrologueLegs:
    """The real `_pre_generic_slot_family`.

    It owns every verdict the row tuple cannot express: the missing slot and
    the two type-param slot shapes, each of which REJECTS what it does not
    name. A concrete slot must fall through, or the whole tail ladder is
    dead.
    """

    def _pre(self, a, open_ptype, resolved, analyzer=None, locals_=None):
        return _pre_generic_slot_family(
            _ArgReq(a, resolved, locals_ or {}, analyzer, frozenset(),
                    frozenset(), False, False, open_ptype=open_ptype))

    def test_a_missing_slot_rejects_with_the_slot_tag(self):
        compiler = _fake_compiler()
        with activate_compiler(compiler):
            assert self._pre(TpyName("x"), None, None) is False
        assert compiler._thir_reject_detail == "call.generic_arg_slot"

    def test_a_still_open_slot_rejects_an_unnamed_source(self):
        # `resolved` still carries T, so the open-slot branch decides -- and
        # a str literal is none of its rows.
        t = TypeParamRef("T")
        compiler = _fake_compiler()
        with activate_compiler(compiler):
            assert self._pre(TpyStrLiteral("a"), t, t) is False
        assert compiler._thir_reject_detail == "call.generic_arg_slot"

    def test_a_still_open_slot_admits_the_same_bare_t_name(self):
        # The bare-T name row: the binding IS the slot, so it passes the
        # form-neutral param bare.
        t = TypeParamRef("T")
        assert self._pre(TpyName("x"), t, t, locals_={"x": t}) is True

    def test_a_bare_type_param_slot_rejects_with_the_shape_tag(self):
        # A resolved-to-scalar T slot: a name the body did not bind is not
        # one of its rows, and the branch's own tag is the SHAPE one.
        compiler = _fake_compiler()
        with activate_compiler(compiler):
            assert self._pre(TpyName("x"), TypeParamRef("T"), INT32) is False
        assert compiler._thir_reject_detail == "call.generic_arg_shape"

    def test_a_concrete_slot_falls_through_to_the_rows(self):
        assert self._pre(TpyName("x"), INT32, INT32) is None


class TestGenericOpenSlot:
    """`open_ptype` -- the UNSUBSTITUTED slot the generic family's prologue
    and borrow-tuple cell ask about, while every row decides against the
    substituted one `ptype` carries."""

    def test_the_open_slot_reaches_the_rows(self):
        seen = []

        def _spy(req):
            seen.append(req.open_ptype)
            return False

        sink = _ArgSink(family="t_gen", note="x.y",
                        rows=(_ArgRow("t_gen1", _spy),))
        with activate_compiler(_fake_compiler()):
            assert arg_ok(sink, None, INT32, {}, None,
                          param_names=frozenset(), narrowed=frozenset(),
                          temps_ok=False,
                          open_ptype=TypeParamRef("T")) is False
        assert seen == [TypeParamRef("T")]

    def test_it_defaults_to_absent_for_every_other_family(self):
        seen = []

        def _spy(req):
            seen.append(req.open_ptype)
            return False

        sink = _ArgSink(family="t_gen2", note="x.y",
                        rows=(_ArgRow("t_gen2a", _spy),))
        assert _call(sink) is False
        assert seen == [None]

class TestRecordMethodContext:
    """The three per-call facts this family added to `_ArgReq`."""

    def test_the_new_fields_reach_the_rows(self):
        seen = []
        sink = _ArgSink(
            family="t_rm", note="x.y",
            rows=(_ArgRow("t_rm1",
                          lambda req: seen.append(
                              (req.index, req.overload,
                               req.frame_capturing)) or False),))
        with activate_compiler(_fake_compiler()):
            assert arg_ok(sink, None, INT32, {}, None,
                          param_names=frozenset(), narrowed=frozenset(),
                          temps_ok=False, index=2, overload="FI",
                          frame_capturing=True) is False
        assert seen == [(2, "FI", True)]

    def test_they_default_to_absent_for_every_other_family(self):
        # A family that does not pass them must see the inert defaults --
        # `index=-1` can match no real argument position, so a row reading
        # it without the caller threading it cannot silently succeed.
        seen = []
        sink = _ArgSink(family="t_rm2", note="x.y",
                        rows=(_ArgRow("t_rm2a",
                                      lambda req: seen.append(
                                          (req.index, req.overload,
                                           req.frame_capturing)) or False),))
        with activate_compiler(_fake_compiler()):
            assert _call(sink) is False
        assert seen == [(-1, None, False)]

class TestContainerPrologue:
    def test_the_family_has_one(self):
        # Two of its three legs REJECT everything they do not name, which a
        # row tuple (cells only ever admit) cannot express.
        assert _METHOD_ARG_SINK.pre is not None

    def test_prologue_verdict_short_circuits_the_rows(self):
        ran = []

        def _row(req):
            ran.append(1)
            return True

        sink = _ArgSink(family="t_pre", note="x.y",
                        pre=lambda req: False,
                        rows=(_ArgRow("t_pre1", _row),))
        assert _call(sink) is False
        assert ran == []

    def test_prologue_none_falls_through_to_the_rows(self):
        sink = _ArgSink(family="t_pre2", note="x.y",
                        pre=lambda req: None,
                        rows=(_ArgRow("t_pre2a", _yes),))
        assert _call(sink) is True

class TestContainerPrologueLegs:
    """The real `_pre_container_slot_family`, not a synthetic stand-in.

    Its three legs are the family's only REJECTING verdicts, and two of them
    fence payloads out of the Own cascade that the row tuple would otherwise
    admit -- the view leg is what leaves `Own[str]` as the only
    position-sensitive payload reaching `own_lvalue` at a stub slot. A
    synthetic `pre=lambda: False` proves the walk consults a prologue; only
    this proves THIS prologue still decides what it decided.
    """

    def _pre(self, a, ptype, analyzer=None):
        return _pre_container_slot_family(
            _ArgReq(a, ptype, {}, analyzer, frozenset(), frozenset(),
                    False, False))

    def test_own_view_slot_rejects_a_non_literal_source(self):
        # `set[StrView].add(name)`: the AST hoists a copy+move view temp,
        # which nothing mirrors -- so the leg rejects ahead of the rows.
        assert self._pre(TpyName("name"), OwnType(STRVIEW)) is False

    def test_own_view_slot_falls_through_for_a_literal(self):
        # The literal insert IS mirrored, so the prologue defers to the rows.
        assert self._pre(TpyStrLiteral("a"), OwnType(STRVIEW)) is None

    def test_wrapper_union_elem_slot_absorbs_a_scalar_literal(self):
        u = UnionType((INT32, CHAR))
        compiler = _fake_compiler()
        compiler.union_wrapper_index = {
            u.members: RecursiveUnionInfo(name="W", full_members=u.members,
                                          origin="m")}
        with activate_compiler(compiler):
            assert self._pre(TpyIntLiteral(4), OwnType(u)) is True
            # ... and every other source keeps the family's named reject.
            assert self._pre(TpyName("x"), OwnType(u)) is False
        assert compiler._thir_face_witnesses == {
            "arg.ru_wrapper_elem_literal": 1}

    def test_plain_union_elem_slot_rejects_an_unnamed_source(self):
        # No wrapper index, so the same union is the PLAIN variant leg: a
        # non-name, non-member-ctor source stays out.
        assert self._pre(TpyIntLiteral(4),
                         OwnType(UnionType((INT32, CHAR)))) is False

    def test_plain_union_elem_slot_admits_a_same_union_local_name(self):
        # A LOCAL name of the same union is the copy/lift the arg arm
        # renders; the leg admits it and the rows never see it.
        u = UnionType((INT32, CHAR))
        analyzer = types.SimpleNamespace(get_expr_type=lambda e: u)
        req = _ArgReq(TpyName("x"), OwnType(u), {"x": u}, analyzer,
                      frozenset(), frozenset(), False, False)
        assert _pre_container_slot_family(req) is True
        # A name the body did not bind is not that shape, so it keeps the
        # family's named reject rather than falling through to the rows.
        assert self._pre(TpyName("x"), OwnType(u), analyzer) is False

    def test_a_slot_no_leg_names_falls_through(self):
        assert self._pre(TpyName("x"), INT32) is None

    def test_a_bare_union_slot_is_not_an_element_slot(self):
        # The head test: the legs are about container ELEMENT slots, every
        # one of which the stubs spell `Own[T]`. A record method's plain
        # union param is the same union without the Own
        # (`datetime.astimezone(tz: timezone | ZoneInfo | None)`), and it
        # must reach the rows -- keyed on the peel-Own-IF-PRESENT slot it
        # took leg 3's reject and the member-ctor argument lost its
        # `union_ctor_temp` cell.
        u = UnionType((INT32, CHAR))
        analyzer = types.SimpleNamespace(get_expr_type=lambda e: u)
        req = _ArgReq(TpyName("x"), u, {"x": u}, analyzer,
                      frozenset(), frozenset(), False, False)
        assert _pre_container_slot_family(req) is None
        assert self._pre(TpyIntLiteral(4), u) is None


class TestRowIdentity:
    def test_duplicate_row_in_one_family_rejected(self):
        with pytest.raises(AssertionError, match="duplicate row"):
            register_sink(_ArgSink(
                family="dup", note="x.y",
                rows=(_ArgRow("t_dup", _yes), _ArgRow("t_dup", _yes))))

    def test_same_row_name_two_predicates_rejected(self):
        register_sink(_ArgSink(family="t_a", note="x.y",
                               rows=(_ArgRow("t_shared", _yes),)))
        with pytest.raises(AssertionError, match="two different predicates"):
            register_sink(_ArgSink(family="t_b", note="x.y",
                                   rows=(_ArgRow("t_shared", _no),)))

    def test_same_row_name_same_predicate_accepted(self):
        register_sink(_ArgSink(family="t_c", note="x.y",
                               rows=(_ArgRow("t_reused", _yes),)))
        register_sink(_ArgSink(family="t_d", note="x.y",
                               rows=(_ArgRow("t_reused", _yes),)))


class TestWalk:
    def test_first_matching_row_wins_and_fires_its_face(self):
        compiler = _fake_compiler()
        sink = _ArgSink(family="t_walk", note="x.y",
                        rows=(_ArgRow("t_w1", _no),
                              _ArgRow("t_w2", _yes,
                                      face="method.protocol_optional_ptr"),
                              _ArgRow("t_w3", _yes)))
        with activate_compiler(compiler):
            assert _call(sink) is True
        assert compiler._thir_face_witnesses == {
            "method.protocol_optional_ptr": 1}

    def test_extra_is_a_pre_guard(self):
        # `extra` replaces the `own_ok and <pred>` prefixes, so it must
        # short-circuit BEFORE the predicate runs.
        ran = []

        def _pred(req):
            ran.append(1)
            return True

        sink = _ArgSink(family="t_extra", note="x.y",
                        rows=(_ArgRow("t_e1", _pred, extra=_no),))
        assert _call(sink) is False
        assert ran == []

    def test_no_match_tags_the_family_note(self):
        compiler = _fake_compiler()
        sink = _ArgSink(family="t_note", note="method.view.arg_shape",
                        rows=(_ArgRow("t_n1", _no),))
        with activate_compiler(compiler):
            assert _call(sink) is False
        assert compiler._thir_reject_detail == "method.view.arg_shape"


class TestWalkProperties:
    """The walk mechanism, swept EXHAUSTIVELY over synthetic sinks.

    Every table in the fold rests on `_walk` behaving one way, and the units
    above each pin one hand-built shape. This enumerates the whole small
    space -- every row tuple up to three cells over (admits/rejects) x (no
    guard / passing guard / failing guard) x (decisive or not), against all
    four prologue legs (absent / admits / rejects / runs-and-falls-through)
    and with/without a family tail -- and asserts the CONTRACT over each:
    what ran, in what order, what fired, what was tagged. A sink shape that
    violates it has nowhere to hide.

    Synthetic sinks are built directly rather than through `register_sink`:
    these row names are throwaway and must not enter the cross-family
    identity registry.
    """

    # One real registered face per position, so an admit can be attributed
    # to the exact cell that fired it.
    FACES = ("method.protocol_optional_ptr", "arg.opt_own_record_rvalue",
             "arg.open_value_tuple_name")

    # `_ArgSink.pre` is not a two-state field: a prologue that RUNS and
    # returns None hands the verdict back to the rows, so it is the leg that
    # can still perturb row order -- the property this class exists to
    # protect. It is swept with the other three rather than pinned by one
    # hand-built shape.
    PROLOGUE_LEGS = ("absent", "admits", "rejects", "falls_through")
    _PRE_VERDICT = {"admits": True, "rejects": False, "falls_through": None}

    def _pre(self, trace, leg):
        """The prologue for `leg` -- None when absent, otherwise one that
        records that it ran before returning its verdict."""
        if leg == "absent":
            return None

        def _p(req, v=self._PRE_VERDICT[leg]):
            trace.append("pre")
            return v
        return _p

    def _rows(self, trace, shapes):
        """Build the row tuple for `shapes`, instrumented to record the order
        in which guards and predicates actually run."""
        rows = []
        for i, (admits, guard, decisive) in enumerate(shapes):
            def _fn(req, i=i, admits=admits):
                trace.append(f"fn{i}")
                return admits

            extra = None
            if guard is not None:
                def _extra(req, i=i, guard=guard):
                    trace.append(f"guard{i}")
                    return guard
                extra = _extra
            rows.append(_ArgRow(f"t_prop{i}", _fn, extra=extra,
                                face=self.FACES[i], decisive=decisive))
        return tuple(rows)

    def _shapes(self, n):
        from itertools import product
        return product(*[list(product((True, False), (None, True, False),
                                      (False, True)))] * n)

    def _expected(self, shapes, leg, note):
        """The contract, restated independently of `_walk`: a prologue that
        exists runs first, and is the whole verdict when it fires; otherwise
        the first row whose guard passes and whose predicate admits wins, a
        decisive rejection ends the walk, and only a fall-through reaches the
        tail.

        Returns (verdict, trace, face, tagged)."""
        trace = [] if leg == "absent" else ["pre"]
        # Absent and falls-through differ only in whether the prologue ran:
        # neither decides, so both go on to the rows.
        verdict = self._PRE_VERDICT.get(leg)
        if verdict is not None:
            return (verdict, trace, None, False)
        for i, (admits, guard, decisive) in enumerate(shapes):
            if guard is False:
                trace.append(f"guard{i}")
                continue
            if guard is True:
                trace.append(f"guard{i}")
            trace.append(f"fn{i}")
            if admits:
                return (True, trace, self.FACES[i], False)
            if decisive:
                return (False, trace, None, False)
        return (False, trace, None, note is not None)

    @pytest.mark.parametrize("n", (1, 2, 3))
    @pytest.mark.parametrize("leg", PROLOGUE_LEGS)
    @pytest.mark.parametrize("note", ("x.y", None))
    def test_the_contract_holds_over_every_small_sink(self, n, leg, note):
        for shapes in self._shapes(n):
            trace = []
            compiler = _fake_compiler()
            sink = _ArgSink(
                family="t_prop", note=note, rows=self._rows(trace, shapes),
                pre=self._pre(trace, leg))
            with activate_compiler(compiler):
                got = _call(sink)
            want, want_trace, face, tagged = self._expected(shapes, leg, note)
            label = f"{shapes} pre={leg} note={note}"
            assert got is want, label
            # Order preservation + short-circuit: the guards and predicates
            # that ran are exactly the contract's prefix, in its order.
            assert trace == want_trace, label
            assert compiler._thir_face_witnesses == (
                {face: 1} if face is not None else {}), label
            assert compiler._thir_reject_detail == (
                note if tagged else None), label

    def test_a_firing_prologue_reaches_no_row_at_all(self):
        # The property above pins it per shape; this states it once as the
        # claim -- a prologue that fired is the family's whole verdict, so
        # not even a row's `extra` guard may run.
        for pre in (True, False):
            for shapes in self._shapes(2):
                trace = []
                sink = _ArgSink(family="t_prop2", note="x.y",
                                rows=self._rows(trace, shapes),
                                pre=lambda req, v=pre: v)
                with activate_compiler(_fake_compiler()):
                    assert _call(sink) is pre
                assert trace == []

    def test_a_prologue_returning_none_runs_before_every_row(self):
        # The property above sweeps this leg per shape; this states it once
        # as the claim -- a prologue that falls through still RAN, ahead of
        # the first row's guard, and left the rows to decide.
        trace = []
        sink = _ArgSink(
            family="t_prop3", note="x.y",
            rows=(_ArgRow("t_prop3a", lambda req: trace.append("fn") or True,
                          extra=lambda req: trace.append("guard") or True),),
            pre=lambda req: trace.append("pre"))
        with activate_compiler(_fake_compiler()):
            assert _call(sink) is True
        assert trace == ["pre", "guard", "fn"]

    @pytest.mark.parametrize("holds", (True, False))
    @pytest.mark.parametrize("is_mutated", (True, False))
    def test_the_family_capability_filters_the_per_call_fact(self, holds,
                                                             is_mutated):
        # `mutated_slots` on `_ArgReq` is the FOLD of the family's capability
        # with this position's fact, so a family that does not carry the rule
        # cannot be handed one by a caller -- the whole point of keeping the
        # capability on the sink rather than at the call site.
        seen = []
        sink = _ArgSink(
            family="t_prop4", note="x.y", mutated_slots=holds,
            rows=(_ArgRow("t_prop4a",
                          lambda req: seen.append(req.mutated_slots)
                          or False),))
        with activate_compiler(_fake_compiler()):
            arg_ok(sink, None, INT32, {}, None, param_names=frozenset(),
                   narrowed=frozenset(), temps_ok=False,
                   is_mutated=is_mutated)
        assert seen == [holds and is_mutated]


class TestRecordCtorSinkShape:
    def test_direct_row_order_is_pinned(self):
        assert [r.row for r in _CTOR_ARG_SINK.rows] == [
            "mutated_container_literal",
            "str_pass_through",
            "own_opt_container_ptr",
            "own_str_literal_bare",
            "own_bytes_literal",
            "shared_pass_through",
            "bytearray_rvalue_ctor",
            "value_opt_scalar_value",
            "value_opt_name_pass",
            "str_literal_value_opt",
            "bytes_literal_value_opt",
            "tuple_literal_value_opt",
            "value_opt_member",
            "none_value_opt",
            "none_unit",
            "tparam_name_pass",
            "str_owned_slot",
            "own_move",
            "own_lvalue",
            "own_move_source_slice",
            "own_record_rvalue",
            "own_tparam_call_rvalue",
            "own_opt_ptr_name_move",
            "opt_own_ptr_opt_name_move",
            "opt_own_record_name",
            "opt_own_container_name",
            "copy_own",
            "copy_open_elem",
            "generic_open_slot_elem",
            "func_ref",
            "async_factory_wrap",
            "callable_value_pass",
            "field_read_ref_ctor",
            "record_elem_subscript",
            "container_comp",
            "container_literal",
            "own_container_literal",
            "own_container_comp",
            "own_container_instantiation",
            "own_container_construct",
            "ru_wrapper_name_no_alias",
            "ru_wrapper_own_literal",
            "own_genrec_literal",
            "union_member_lift",
            "union_ctor_temp",
            "union_pass_through",
            "union_coerced_literal",
            "own_union_ctor",
            "protocol_union",
            "protocol_union_literal_temp",
            "protocol_union_iter_temp",
            "own_optional_record_rvalue",
            "value_record_rvalue",
            "value_union_temp",
            "value_union_narrowed_pass",
            "tuple_literal",
            "lambda",
            "optional_ptr",
            "protocol_slot_ctor",
            "dyn_own_conformer",
            "record_rvalue_temp_ctor",
            "wide_opt_deref_name",
        ]

    def test_nested_row_order_is_pinned(self):
        assert [r.row for r in _CTOR_NESTED_ARG_SINK.rows] == [
            "own_str_literal_bare",
            "none_value_opt",
            "str_literal_value_opt",
            "value_opt_member",
            "own_record_rvalue",
            "own_move",
            "own_move_source_slice",
            "dyn_own_conformer",
            "value_record_rvalue",
            "ptr_pass_through",
            "record_elem_subscript",
            "wide_opt_deref_name",
            "shared_pass_through",
            "container_field_pass",
            "record_field_marker",
            "container_literal_method",
            "optional_ptr",
            "protocol_slot",
            "const_rvalue",
            "str_pass_through_unmutated",
        ]

    def test_neither_family_spells_a_reject_tail(self):
        # The three ctor gates tag their own rejects
        # (`call.ctor_arg.<family>` / `ctor.arg.<family>`), and `note_detail`
        # is set-if-empty -- a tail here would take the tag those sites
        # record, which `probe_sites.py` histograms on.
        assert _CTOR_ARG_SINK.note is None
        assert _CTOR_NESTED_ARG_SINK.note is None

    def test_they_are_the_only_families_carrying_the_mutated_policy(self):
        # Only the ctor consults the callee's `mutated_params` and declines
        # a mutated `String&` slot; the sibling families mirror the same AST
        # render without the rule. Turning it on for them moves which bodies
        # route, so it is a change of its own.
        assert _CTOR_ARG_SINK.mutated_slots is True
        assert _CTOR_NESTED_ARG_SINK.mutated_slots is True
        for sink in (_PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _METHOD_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK,
                     _MARKER_NATIVE_ARG_SINK, _MARKER_TEMPLATE_ARG_SINK,
                     _PLAIN_ARG_SINK, _GENERIC_PLAIN_ARG_SINK):
            assert sink.mutated_slots is False, sink.family

    def test_witnessing_cells_are_pinned(self):
        assert [(r.row, r.face) for r in _CTOR_ARG_SINK.rows
                if r.face is not None] == [
            ("str_pass_through", "ctor.str_arg"),
            ("own_str_literal_bare", "ctor.own_str_literal"),
            ("own_bytes_literal", "ctor.own_bytes_literal"),
            ("bytearray_rvalue_ctor", "ctor.bytearray_rvalue"),
            ("value_opt_name_pass", "ctor.value_opt_pass_arg"),
            ("bytes_literal_value_opt", "ctor.bytes_literal_value_opt"),
            ("tuple_literal_value_opt", "ctor.tuple_literal_value_opt"),
            ("own_move", "ctor.own_arg"),
            ("own_lvalue", "ctor.own_arg"),
            ("own_move_source_slice", "ctor.own_arg"),
            ("opt_own_container_name", "ctor.opt_own_container_name"),
            ("generic_open_slot_elem", "ctor.generic_open_slot_elem"),
            ("field_read_ref_ctor", "ctor.field_read_ref_arg"),
            ("container_literal", "ctor.container_literal_arg"),
            ("own_container_literal", "ctor.container_literal_arg"),
            ("ru_wrapper_own_literal", "ctor.ru_wrapper_own_literal"),
            ("union_pass_through", "ctor.union_pass_arg"),
            ("protocol_union", "ctor.protocol_union_arg"),
            ("lambda", "ctor.lambda_arg"),
        ]
        assert [(r.row, r.face) for r in _CTOR_NESTED_ARG_SINK.rows
                if r.face is not None] == [
            ("own_str_literal_bare", "ctor.own_str_literal"),
            ("none_value_opt", "ctor.nested_none_value_opt"),
            ("str_literal_value_opt", "ctor.nested_none_value_opt"),
            ("own_move", "ctor.own_arg"),
            ("own_move_source_slice", "ctor.own_arg"),
            ("const_rvalue", "ctor.const_rvalue_arg"),
            ("str_pass_through_unmutated", "ctor.str_arg"),
        ]

    def test_guarded_cells_are_pinned(self):
        assert [(r.row, r.extra.__name__) for r in _CTOR_ARG_SINK.rows
                if r.extra is not None] == [
            ("mutated_container_literal", "_x_temps_and_mutated"),
            ("own_lvalue", "_x_temps_ok"),
            ("own_move_source_slice", "_x_not_temps_ok"),
            ("container_comp", "_x_temps_ok"),
            ("container_literal", "_x_not_mutated"),
            ("union_ctor_temp", "_x_temps_ok"),
            ("protocol_union_literal_temp", "_x_temps_ok"),
            ("protocol_union_iter_temp", "_x_temps_ok"),
            ("value_union_temp", "_x_temps_ok"),
        ]
        assert [(r.row, r.extra.__name__) for r in _CTOR_NESTED_ARG_SINK.rows
                if r.extra is not None] == [
            ("container_literal_method", "_x_not_mutated"),
            ("const_rvalue", "_x_ctor_const_rvalue_slot"),
            ("str_pass_through_unmutated", "_x_ctor_str_source_allowed"),
        ]

    def test_one_decisive_cell_in_the_whole_table(self):
        # The nested tail's un-mutated record-rvalue slot block, the only
        # cell in the fold that REJECTS at a POSITION rather than falling
        # through. Every other family expresses its rejects in a prologue.
        decisive = [(s.family, r.row)
                    for s in (_CTOR_ARG_SINK, _CTOR_NESTED_ARG_SINK,
                              _PROTOCOL_ARG_SINK,
                              _NATIVE_ARG_SINK, _METHOD_ARG_SINK,
                              _MARKER_QUALIFIED_ARG_SINK,
                              _MARKER_NATIVE_ARG_SINK,
                              _MARKER_TEMPLATE_ARG_SINK, _PLAIN_ARG_SINK,
                              _GENERIC_PLAIN_ARG_SINK)
                    for r in s.rows if r.decisive]
        assert decisive == [("record_ctor_nested", "const_rvalue")]

    def test_only_the_nested_family_has_a_prologue(self):
        # The direct family's ladder opened straight into its chain; the
        # nested one opened with two slot-keyed early returns.
        assert _CTOR_ARG_SINK.pre is None
        assert _CTOR_NESTED_ARG_SINK.pre is _pre_ctor_nested_slot_family


class TestRecordCtorNestedIsNotADirectPrefix:
    """The design's structural prediction, checked rather than assumed."""

    def test_the_nested_family_is_not_a_subset_of_the_direct_one(self):
        direct = {r.row for r in _CTOR_ARG_SINK.rows}
        nested = {r.row for r in _CTOR_NESTED_ARG_SINK.rows}
        # Nested-only cells, all of which the direct family answers
        # elsewhere: `const_rvalue` is the decisive slot block (direct
        # spells the same slot as the flush-gated `record_rvalue_temp_ctor`
        # at the very end of its tuple), `str_pass_through_unmutated`
        # hardcodes `mutated=False` where direct threads the fact,
        # `ptr_pass_through` is one leg of the direct family's
        # `shared_pass_through` bundle (taken alone before that bundle
        # joined it), and the four member/literal/protocol reads are the
        # sibling families' cells for shapes the direct ctor spells with
        # its own predicates (`field_read_ref_ctor`, `container_literal`,
        # `protocol_slot_ctor`).
        assert nested - direct == {"const_rvalue",
                                   "str_pass_through_unmutated",
                                   "ptr_pass_through",
                                   "container_field_pass",
                                   "record_field_marker",
                                   "container_literal_method",
                                   "protocol_slot"}

    def test_the_direct_cells_absent_from_nested_are_named_and_counted(self):
        # The flush-less nested position admits only temp-FREE renders, so
        # the direct cells whose render hoists stay absent. The count is
        # what closes the gap -- naming a subset leaves the unnamed
        # absences free to be filled silently later.
        direct = {r.row for r in _CTOR_ARG_SINK.rows}
        nested = {r.row for r in _CTOR_NESTED_ARG_SINK.rows}
        assert len(direct - nested) == 49
        assert {"mutated_container_literal", "str_pass_through",
                "own_lvalue", "own_bytes_literal",
                "container_literal", "own_container_literal",
                "union_ctor_temp", "union_member_lift", "value_union_temp",
                "protocol_slot_ctor", "tuple_literal",
                "lambda", "func_ref", "record_rvalue_temp_ctor",
                "protocol_union", "protocol_union_literal_temp",
                "protocol_union_iter_temp",
                "own_opt_container_ptr", "opt_own_record_name",
                } <= (direct - nested)

    def test_the_shared_cells_sit_in_a_different_relative_order(self):
        # Row order IS the face census, so this is not cosmetic: the nested
        # tail runs `own_record_rvalue` before `own_move`, where the direct
        # tail runs the Own cascade first.
        direct = [r.row for r in _CTOR_ARG_SINK.rows]
        nested = [r.row for r in _CTOR_NESTED_ARG_SINK.rows]
        shared = set(direct) & set(nested)
        assert [r for r in direct if r in shared] != [
            r for r in nested if r in shared]


class TestRecordCtorSharedAndNewRows:
    def test_the_shared_rows_reach_the_other_families_cells(self):
        # `register_sink` has already proved each of these reaches the
        # IDENTICAL predicate (it fails the import otherwise) -- across
        # MODULES here, which is the property that makes a sink built in
        # `expressions.py` as safe as one built beside the tables.
        others = set()
        for sink in (_PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _METHOD_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK,
                     _PLAIN_ARG_SINK, _GENERIC_PLAIN_ARG_SINK):
            others |= {r.row for r in sink.rows}
        rows = [r.row for r in _CTOR_ARG_SINK.rows]
        assert [r for r in rows if r not in others] == [
            "mutated_container_literal",
            "own_opt_container_ptr",
            "own_str_literal_bare",
            "own_bytes_literal",
            "bytearray_rvalue_ctor",
            "value_opt_name_pass",
            "tparam_name_pass",
            "own_move_source_slice",
            "opt_own_container_name",
            "copy_open_elem",
            "generic_open_slot_elem",
            "async_factory_wrap",
            "field_read_ref_ctor",
            "own_container_instantiation",
            "own_container_construct",
            "ru_wrapper_name_no_alias",
            "ru_wrapper_own_literal",
            "own_genrec_literal",
            "protocol_union",
            "protocol_union_literal_temp",
            "protocol_union_iter_temp",
            "protocol_slot_ctor",
            "record_rvalue_temp_ctor",
        ]
        assert len([r for r in rows if r in others]) == 39

    def test_the_shadow_rows_hold_a_different_predicate(self):
        # Each of these SHADOWS a shared row name and had to be given its
        # own, because the ctor spelling admits a different set. Pinned so a
        # later step cannot quietly collapse one onto the shared cell --
        # `register_sink` catches the reverse mistake (one name, two
        # predicates), never this one.
        cells = {r.row: r.fn for s in (_CTOR_ARG_SINK,
                                       _CTOR_NESTED_ARG_SINK)
                 for r in s.rows}
        shared = {r.row: r.fn for s in (_PLAIN_ARG_SINK,
                                        _GENERIC_PLAIN_ARG_SINK,
                                        _METHOD_ARG_SINK,
                                        _MARKER_QUALIFIED_ARG_SINK)
                  for r in s.rows}
        for ctor_row, shared_row in (
                # peels the coerce and unwraps the slot; this one does not
                ("own_str_literal_bare", "own_str_literal"),
                # passes `analyzer`, so the alias placeholder resolves
                ("ru_wrapper_name_no_alias", "ru_wrapper_name"),
                # falls back to the analyzer for an unbound name
                ("value_opt_name_pass", "value_opt_pass_through"),
                # `upcast_ok=True` -- a CHILD-typed temp
                ("record_rvalue_temp_ctor", "record_rvalue_temp"),
                # threads the mutation fact instead of refusing outright
                ("str_pass_through_unmutated", "str_pass_through"),
                # admits a structural RVALUE, which is ctor-inline here
                ("protocol_slot_ctor", "protocol_slot")):
            assert cells[ctor_row] is not shared[shared_row], ctor_row


class TestRecordCtorFork:
    """Which sink the ctor USE selects -- the marker family's
    dispatch-to-multiple-sinks shape, not one tuple serving two positions."""

    def _sink_for(self, monkeypatch, ctor_use, allow_temps):
        picked = []
        monkeypatch.setattr(
            expressions, "arg_ok",
            lambda sink, *a, **kw: picked.append(sink) or False)
        lc = types.SimpleNamespace(
            analyzer=None, prescan=types.SimpleNamespace(
                param_names=frozenset()),
            narrow=types.SimpleNamespace(narrowed=frozenset()),
            inline_narrowed={}, movable_locals=set(), pointers=set(),
            func=None)
        expressions._record_ctor_arg_supported(
            TpyName("x"), INT32, 0, None, lc, {},
            _ExprUse(allow_temps=allow_temps, record_ctor=ctor_use))
        return picked[0]

    def test_a_direct_position_takes_the_full_family(self, monkeypatch):
        assert self._sink_for(
            monkeypatch, _RecordCtorUse.DIRECT, False) is _CTOR_ARG_SINK

    def test_a_record_temp_source_takes_the_full_family(self, monkeypatch):
        assert self._sink_for(
            monkeypatch, _RecordCtorUse.RECORD_TEMP, False) is _CTOR_ARG_SINK

    def test_a_flushable_nested_position_takes_the_full_family(self,
                                                               monkeypatch):
        # A NESTED_ARG ctor that rode the enclosing statement's flush right
        # in gates like DIRECT: its temps flush at the same statement.
        assert self._sink_for(
            monkeypatch, _RecordCtorUse.NESTED_ARG, True) is _CTOR_ARG_SINK

    def test_only_a_flushless_nested_position_takes_the_restricted_family(
            self, monkeypatch):
        assert self._sink_for(
            monkeypatch, _RecordCtorUse.NESTED_ARG,
            False) is _CTOR_NESTED_ARG_SINK


class TestRecordCtorContext:
    """The per-call facts this family added to `_ArgReq` -- the LOWERING
    CONTEXT's, extracted as discrete values because `arg_table` deliberately
    carries no `_LowerCtx`."""

    def test_the_new_fields_reach_the_rows(self):
        seen = []

        def _cap(req):
            seen.append(
                (req.mutated_slots, req.mutation_unknown,
                 sorted(req.inline_narrowed), sorted(req.movable_locals),
                 sorted(req.pointers), req.func_name))
            return False

        sink = _ArgSink(family="t_ctor", note="x.y",
                        mutated_slots=True,
                        rows=(_ArgRow("t_ctor1", _cap),))
        with activate_compiler(_fake_compiler()):
            assert arg_ok(sink, None, INT32, {}, None,
                          param_names=frozenset(), narrowed=frozenset(),
                          temps_ok=False, is_mutated=True,
                          mutation_unknown=True,
                          inline_narrowed={"n": ("N", True)},
                          movable_locals={"m"}, pointers={"p"},
                          func_name="fn") is False
        assert seen == [(True, True, ["n"], ["m"], ["p"], "fn")]

    def test_they_default_to_absent_for_every_other_family(self):
        seen = []
        sink = _ArgSink(family="t_ctor2", note="x.y",
                        rows=(_ArgRow("t_ctor2a",
                                      lambda req: seen.append(
                                          (req.mutation_unknown,
                                           req.inline_narrowed,
                                           req.movable_locals, req.pointers,
                                           req.func_name)) or False),))
        with activate_compiler(_fake_compiler()):
            assert _call(sink) is False
        assert seen == [(False, frozenset(), frozenset(), frozenset(), None)]

    def test_is_mutated_only_reaches_a_family_that_holds_the_policy(self):
        # `mutated_slots` on `_ArgReq` is the FOLD of the family capability
        # with this argument's position, so a family that does not carry the
        # rule cannot be handed one by a caller.
        seen = []
        row = _ArgRow("t_ctor3a",
                      lambda req: seen.append(req.mutated_slots) or False)
        off = _ArgSink(family="t_ctor3", note="x.y", rows=(row,))
        on = _ArgSink(family="t_ctor4", note="x.y", mutated_slots=True,
                      rows=(_ArgRow("t_ctor3a", row.fn),))
        with activate_compiler(_fake_compiler()):
            for sink in (off, on):
                arg_ok(sink, None, INT32, {}, None, param_names=frozenset(),
                       narrowed=frozenset(), temps_ok=False, is_mutated=True)
        assert seen == [False, True]


class TestDecisiveCell:
    def test_a_decisive_reject_ends_the_walk(self):
        # The ladder spelled this cell as an early `return False`, so the
        # rows below it must not get a look -- that is the whole difference
        # between a decisive cell and an ordinary one.
        ran = []
        sink = _ArgSink(
            family="t_dec", note="x.y",
            rows=(_ArgRow("t_dec1", _no, extra=_yes, decisive=True),
                  _ArgRow("t_dec2", lambda req: ran.append(1) or True)))
        with activate_compiler(_fake_compiler()):
            assert _call(sink) is False
        assert ran == []

    def test_a_decisive_cell_whose_guard_misses_falls_through(self):
        sink = _ArgSink(
            family="t_dec2", note="x.y",
            rows=(_ArgRow("t_dec2a", _no, extra=_no, decisive=True),
                  _ArgRow("t_dec2b", _yes)))
        with activate_compiler(_fake_compiler()):
            assert _call(sink) is True

    def test_a_decisive_reject_does_not_tag_the_family_note(self):
        # The ladder's bare `return False` reached no note either, and
        # `note_detail` is set-if-empty -- a tag here would displace the
        # caller's.
        compiler = _fake_compiler()
        sink = _ArgSink(
            family="t_dec3", note="method.view.arg_shape",
            rows=(_ArgRow("t_dec3a", _no, extra=_yes, decisive=True),))
        with activate_compiler(compiler):
            assert _call(sink) is False
        assert compiler._thir_reject_detail is None


class TestNoteless:
    def test_a_family_without_a_tail_leaves_the_reject_detail_alone(self):
        compiler = _fake_compiler()
        sink = _ArgSink(family="t_noteless", note=None,
                        rows=(_ArgRow("t_nl", _no),))
        with activate_compiler(compiler):
            assert _call(sink) is False
        assert compiler._thir_reject_detail is None


class TestReachTally:
    """The reach tally: which CELL decided, recorded per compilation.

    Not a second implementation of anything -- it records the walk's own
    verdict site. It exists because a family nothing dispatches to is
    invisible to every other gate: it emits no C++, so the byte-diff is
    silent, and its rows carry no faces of their own for the zero-witness
    census to miss. `tests/test_thir_stdlib_gate.py` turns it into a
    per-family floor over the whole stdlib sweep.

    Synthetic sinks are built directly rather than through `register_sink`,
    as in `TestWalkProperties`: these row names are throwaway.
    """

    def test_the_admitting_cell_is_what_is_recorded(self):
        compiler = _fake_compiler()
        sink = _ArgSink(family="t_reach", note="x.y",
                        rows=(_ArgRow("t_r1", _no), _ArgRow("t_r2", _yes),
                              _ArgRow("t_r3", _yes)))
        with activate_compiler(compiler):
            assert _call(sink) is True
        # The cell that DECIDED, not every cell the walk touched: t_r1 ran
        # and said no, which is not a witness that its shape is admitted
        # anywhere.
        assert reached(compiler) == {("t_reach", "t_r2"): 1}

    def test_a_fall_through_records_the_family_but_no_cell(self):
        compiler = _fake_compiler()
        sink = _ArgSink(family="t_reach2", note="x.y",
                        rows=(_ArgRow("t_r4", _no),))
        with activate_compiler(compiler):
            assert _call(sink) is False
        assert reached(compiler) == {("t_reach2", NO_CELL): 1}

    def test_a_prologue_verdict_records_the_prologue(self):
        compiler = _fake_compiler()
        sink = _ArgSink(family="t_reach3", note="x.y",
                        rows=(_ArgRow("t_r5", _yes),),
                        pre=lambda req: False)
        with activate_compiler(compiler):
            assert _call(sink) is False
        assert reached(compiler) == {("t_reach3", PROLOGUE_CELL): 1}

    def test_a_decisive_reject_is_its_own_key(self):
        # A decisive cell that only ever rejects is NOT a witness that its
        # admitting leg is reachable, so it must not read as one -- that
        # distinction is exactly what the coverage question asks.
        compiler = _fake_compiler()
        sink = _ArgSink(family="t_reach4", note="x.y",
                        rows=(_ArgRow("t_r6", _no, extra=_yes, decisive=True),
                              _ArgRow("t_r7", _yes)))
        with activate_compiler(compiler):
            assert _call(sink) is False
        assert reached(compiler) == {("t_reach4", "!t_r6"): 1}

    def test_counts_accumulate_and_families_are_derived(self):
        compiler = _fake_compiler()
        sink = _ArgSink(family="t_reach5", note="x.y",
                        rows=(_ArgRow("t_r8", _yes),))
        other = _ArgSink(family="t_reach6", note="x.y",
                         rows=(_ArgRow("t_r9", _no),))
        with activate_compiler(compiler):
            _call(sink)
            _call(sink)
            _call(other)
        assert reached(compiler)[("t_reach5", "t_r8")] == 2
        assert reached_families(compiler) == {"t_reach5", "t_reach6"}

    def test_it_is_a_no_op_outside_a_compilation(self):
        # Same rule `witness` follows: recording is a tally, never a
        # precondition of admission.
        sink = _ArgSink(family="t_reach7", note=None,
                        rows=(_ArgRow("t_r10", _yes),))
        token = _current_compiler.set(None)
        try:
            assert _call(sink) is True
        finally:
            _current_compiler.reset(token)

    def test_every_real_sink_is_in_the_registry(self):
        # The expected set the gate asserts on is DERIVED from this, so a
        # sink that stopped registering would silently shrink it. Subset,
        # not equality: TestRowIdentity registers throwaways in-process.
        families = set(registered_families())
        for sink in (_PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _METHOD_ARG_SINK, _MARKER_TEMPLATE_ARG_SINK,
                     _MARKER_NATIVE_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK,
                     _PLAIN_ARG_SINK, _GENERIC_PLAIN_ARG_SINK,
                     _CTOR_ARG_SINK, _CTOR_NESTED_ARG_SINK):
            assert sink.family in families
            cells = registered_cells()
            for row in sink.rows:
                assert (sink.family, row.row) in cells


# One representative slot per row of `_decl_slot_forms`, plus the shape just
# outside each -- the decl arms hand it nine differently-named locals, so the
# table names the SHAPE and not the arm. `analyzer` is None: the only row
# that consults it is the `Ptr[T]` one, and a SCALAR pointee answers before
# `_f1_record` is reached, so no registry is needed here (a record pointee is
# the corpus's business).
_REC = NominalType("Rec")
_PTR_OPT_REC = OptionalType(_REC)
_BORROW_TUPLE = TupleType((INT32, _PTR_OPT_REC))
_VALUE_TUPLE = TupleType((INT32, INT32))
_CORO = ConcreteCoroType(name="Cancellable", type_args=(INT32,),
                         coro_func_name="produce")

_DECL_SLOT_ROWS = (
    # (what it stands for, slot, whole_tuple_call, from_call, ptr_local,
    #  expected forms)
    # GROWTH POINT: a `Ptr[<record>]` row would reach `_f1_record` and needs a
    # real analyzer -- the driver asserts every Ptr pointee here is a scalar,
    # so such a row fails loudly rather than answering off a None registry.
    ("coro frame slot off a call", OwnType(_CORO), False, True, False,
     frozenset({SinkForm.CORO_FACTORY})),
    ("coro frame slot, Own already peeled", _CORO, False, True, False,
     frozenset({SinkForm.CORO_FACTORY})),
    # The factory FORM is a call result, so a coro slot with no call takes it
    # away again.
    ("coro frame slot, no call", OwnType(_CORO), False, False, False,
     frozenset()),
    ("ptr-repr Optional record, NAME source", _PTR_OPT_REC, False, False,
     True, frozenset({SinkForm.PTR_OPT_PASSTHROUGH})),
    ("Ptr[T] value slot", PtrType(INT32), False, False, False,
     frozenset({SinkForm.PTR_OPT_PASSTHROUGH})),
    ("value Optional -- not a pointer slot", OptionalType(INT32), False,
     False, False, frozenset()),
    # A tuple of OWNED tuples has no pointer-repr element, which is why this
    # row takes the arm's whole-consumption predicate and not the shape.
    ("value tuple the sink takes whole", _VALUE_TUPLE, True, True, False,
     frozenset({SinkForm.TUPLE_SOURCE})),
    ("value tuple off a call, not taken whole", _VALUE_TUPLE, False, True,
     False, frozenset()),
    ("borrow-form tuple off a call", _BORROW_TUPLE, False, True, False,
     frozenset({SinkForm.BTUPLE_SLOT})),
    ("borrow-form tuple off a read", _BORROW_TUPLE, False, False, False,
     frozenset()),
    # An Optional[tuple] uses pointer repr (its inner is not a value type),
    # so the pointer row would swallow it if the tuple rows did not answer
    # first.
    ("Optional[borrow tuple] taken whole", OptionalType(_BORROW_TUPLE), True,
     True, False, frozenset({SinkForm.TUPLE_SOURCE})),
    ("Optional[borrow tuple] off a call", OptionalType(_BORROW_TUPLE), False,
     True, False, frozenset({SinkForm.BTUPLE_SLOT})),
    ("plain non-value bound as a pointer off a call", _REC, False, True, True,
     frozenset({SinkForm.BORROW_RET_PASSTHROUGH})),
    ("the same slot with Own to peel", OwnType(_REC), False, True, True,
     frozenset({SinkForm.BORROW_RET_PASSTHROUGH})),
    ("plain non-value off a call, not a pointer local", _REC, False, True,
     False, frozenset()),
    ("plain non-value pointer local, no call", _REC, False, False, True,
     frozenset()),
    ("a value scalar takes nothing", INT32, False, True, True, frozenset()),
    ("no slot at all", None, True, True, True, frozenset()),
)


def _decl_slot_forms_produced():
    """The forms `_decl_slot_forms` actually returns over the shape table."""
    out = frozenset()
    with activate_compiler(_fake_compiler()):
        for _what, slot, whole, from_call, ptr_local, _want in \
                _DECL_SLOT_ROWS:
            out |= _decl_slot_forms(slot, None, whole_tuple_call=whole,
                                    from_call=from_call,
                                    ptr_local=ptr_local)
    return out


class TestSinkVocabulary:
    """The sink position and form vocabulary is a closed table: a member added
    to one enum without its row or its admission is caught here, not at the
    first body that reaches it."""

    def test_decl_slot_forms_over_the_shape_table(self):
        # The corpus is the witness for which shapes REACH each row; this
        # pins what the helper answers for each of them, including the two
        # orderings a reader gets wrong from the type system alone: a tuple
        # slot is answered before the pointer row, and TUPLE_SOURCE keys on
        # whole-consumption rather than on the tuple's borrow form.
        # `analyzer=None` holds only while every Ptr pointee answers inside
        # `_eligible_ptr_value` ahead of its `_f1_record` arm -- a value-typed
        # pointee does, a record one would ask the registry.
        for what, slot, *_rest in _DECL_SLOT_ROWS:
            if isinstance(slot, PtrType):
                assert slot.pointee.is_value_type(), (
                    what, "a Ptr[<record>] row needs a real analyzer")
        with activate_compiler(_fake_compiler()):
            for what, slot, whole, from_call, ptr_local, want in \
                    _DECL_SLOT_ROWS:
                got = _decl_slot_forms(slot, None, whole_tuple_call=whole,
                                       from_call=from_call,
                                       ptr_local=ptr_local)
                assert got == want, (what, got, want)

    def test_call_arg_forms_default_is_the_sink_row(self):
        # The helper's all-false result must BE the row, or the nine
        # argument sites that pass no `forms=` would take a different
        # verdict from the six that ask the helper.
        assert _call_arg_forms(False, False, False) == _POS_FORMS[
            SinkPos.CALL_ARG]

    def test_pos_forms_is_total_over_sink_pos(self):
        assert set(_POS_FORMS) == set(SinkPos)

    def test_unspecified_admits_nothing(self):
        """UNSPECIFIED is not a sink, it is the row every unnamed site
        inherits -- and binding sites are among them. So it admits NO form,
        the dying-source lend included: a transient read that wants one names
        its sink."""
        assert _POS_FORMS[SinkPos.UNSPECIFIED] == frozenset()
        assert not any(_ExprUse().admits(f) for f in SinkForm)

    def test_no_binding_sink_admits_a_dying_source_lend(self):
        """The rule, as a table: a sink that BINDS or HOLDS its value past
        the full expression can hold no borrow of storage that expression
        kills. Stated here so a sink added without a considered row fails
        rather than silently admitting one."""
        binding = {
            # Not a binding sink but the unnamed row, which binding sites
            # inherit -- it fails closed with them.
            SinkPos.UNSPECIFIED,
            SinkPos.LOCAL_DECL, SinkPos.CALL_ARG, SinkPos.SETITEM_VALUE,
            SinkPos.TUPLE_ELEM, SinkPos.RETURN, SinkPos.IF_EXPR_ARM,
            SinkPos.CONTAINER_ELEM, SinkPos.FIELD_WRITE, SinkPos.MIL_INIT,
            SinkPos.GLOBAL_SLOT_WRITE, SinkPos.UNPACK_SOURCE,
            SinkPos.ALIAS_BIND, SinkPos.LAMBDA_RETURN,
            SinkPos.WALRUS_TARGET, SinkPos.FRAME_SLOT_WRITE,
            SinkPos.MATCH_SUBJECT, SinkPos.WITH_MANAGER,
            SinkPos.ITER_SOURCE,
        }
        transient = set(SinkPos) - binding
        for pos in binding:
            assert SinkForm.DYING_SOURCE_LEND not in _POS_FORMS[pos], pos
        for pos in transient:
            assert SinkForm.DYING_SOURCE_LEND in _POS_FORMS[pos], pos

    def test_no_forms_site_readmits_the_lend_at_a_binding_sink(self):
        """`forms=` REPLACES a sink's row, so a site can hand itself a verdict
        its sink refuses. The partition test above reads `_POS_FORMS` alone
        and is blind to exactly that: two sites once spelled
        `forms=_LEND_OK` at CALL_ARG, a binding sink, and one of them admitted
        `reversed(mk().items)`. Both are gone -- the argument sink's verdict
        comes from `arg_lend_ok` now -- which is why the exception below is
        spelled as the predicate's name and not as a site's.

        So this is a SOURCE scan rather than a table read: every
        `_ExprUse(...)` / `replace(...)` group in `tpyc/thir/lower` whose
        `forms=` can mention the lend must name a TRANSIENT sink, spelled as
        a literal `pos=SinkPos.X` in the same group -- unless the verdict came
        from `arg_lend_ok`, which is the one sanctioned way an argument admits
        it (it asks the CALLEE's retention facts, not the position; see
        `arg_lend_ok` in lower/expressions.py and
        `BUGS.md#native-stub-declares-no-param-retention`).

        "Can mention" and "spelled as a literal" are both wider than they read.
        A `forms=` that calls a context.py helper whose body names the lend
        (`_recv_forms`) counts, or the check would be blind to a verdict that
        arrives one call away. And a group that hands out the lend while
        INHERITING its `pos` from a replaced base, or naming it through a
        variable, is an offender in itself: the sink cannot be read at the
        site, so the verdict cannot be checked there at all. No such group
        exists today; the rule is what keeps it that way.
        """
        import io
        import pathlib
        import re
        import tokenize
        lowering = pathlib.Path(__file__).resolve().parent / "lower"
        binding = {p.name for p in SinkPos
                   if SinkForm.DYING_SOURCE_LEND not in _POS_FORMS[p]}
        # The names a `forms=` value may mention to mean "the argument sink's
        # verdict, taken from the callee" -- `_lend_forms` is the local
        # `_lower_free_call_arg` computes from `arg_lend_ok` once and hands to
        # each of its arms.
        VIA_PREDICATE = {"arg_lend_ok", "_lend_forms"}
        LEND = {"_LEND_OK", "DYING_SOURCE_LEND"}
        # ... plus any context.py helper whose own body can return the lend:
        # `forms=_recv_forms(..)` hands it out just as `forms=_LEND_OK` does.
        ctx_src = (lowering / "context.py").read_text()
        for helper in re.findall(r"^def (_\w+)\(", ctx_src, re.M):
            body = ctx_src.split(f"def {helper}(", 1)[1].split("\ndef ", 1)[0]
            if any(name in body for name in LEND):
                LEND.add(helper)
        offenders = []
        for f in sorted(lowering.glob("*.py")):
            if f.name == "context.py":
                continue
            toks = [t for t in tokenize.generate_tokens(
                io.StringIO(f.read_text()).readline)
                    if t.type in (tokenize.NAME, tokenize.OP)]
            for i, t in enumerate(toks):
                if t.string not in ("_ExprUse", "replace"):
                    continue
                if i + 1 >= len(toks) or toks[i + 1].string != "(":
                    continue
                depth, j = 0, i + 1
                group = []
                while j < len(toks):
                    st = toks[j].string
                    if st in "([{":
                        depth += 1
                    elif st in ")]}":
                        depth -= 1
                        if depth == 0:
                            break
                    group.append((depth, toks[j]))
                    j += 1
                pos_name = None
                in_forms = False
                mentions_lend = False
                via_pred = False
                for k, (d, tk) in enumerate(group):
                    if d == 1 and tk.string == "pos" and k + 3 < len(group):
                        # pos = SinkPos . NAME
                        pos_name = group[k + 4][1].string if (
                            group[k + 3][1].string == ".") else None
                    if d == 1 and tk.string == "forms":
                        in_forms = True
                    elif d == 1 and tk.string == ",":
                        in_forms = False
                    if in_forms and tk.string in LEND:
                        mentions_lend = True
                    if in_forms and tk.string in VIA_PREDICATE:
                        via_pred = True
                if mentions_lend and not via_pred:
                    if pos_name is None:
                        offenders.append(f"{f.name}:{toks[i].start[0]} "
                                         f"pos not spelled at the site")
                    elif pos_name in binding:
                        offenders.append(f"{f.name}:{toks[i].start[0]} "
                                         f"pos=SinkPos.{pos_name}")
        assert not offenders, (
            "a `forms=` site re-admits SinkForm.DYING_SOURCE_LEND at a sink "
            "whose row refuses it, or at one it does not name; the verdict at "
            "an argument belongs to `arg_lend_ok`, and at any other binding "
            "sink there is no verdict to hand out: " + "; ".join(offenders))

    def test_every_form_is_admitted_somewhere(self):
        # A form is alive only through a PRODUCER: a `_POS_FORMS` row, or a
        # `forms=` argument at a lowering site (a literal, or a named set from
        # context.py that some lowering file actually names). A consumer's
        # `admits(SinkForm.X)` does not count -- a form nobody produces is
        # dead vocabulary even if an arm still asks for it.
        import io
        import pathlib
        import tokenize
        from .lower import context as ctx_mod
        lowering = pathlib.Path(__file__).resolve().parent / "lower"
        named_sets = {name: value for name, value in vars(ctx_mod).items()
                      if isinstance(value, frozenset) and value
                      and all(isinstance(f, SinkForm) for f in value)}
        produced = {f for forms in _POS_FORMS.values() for f in forms}
        for f in lowering.glob("*.py"):
            if f.name == "context.py":
                continue
            toks = [t for t in tokenize.generate_tokens(
                io.StringIO(f.read_text()).readline)
                    if t.type in (tokenize.NAME, tokenize.OP)]
            i = 0
            while i < len(toks) - 1:
                if toks[i].string == "forms" and toks[i + 1].string == "=":
                    depth = 0
                    j = i + 2
                    while j < len(toks):
                        t = toks[j].string
                        if t in "([{":
                            depth += 1
                        elif t in ")]}":
                            if depth == 0:
                                break
                            depth -= 1
                        elif t == "," and depth == 0:
                            break
                        if toks[j].type == tokenize.NAME:
                            if t in named_sets:
                                produced |= named_sets[t]
                            elif (j >= 2 and toks[j - 1].string == "."
                                  and toks[j - 2].string == "SinkForm"):
                                produced.add(SinkForm[t])
                        j += 1
                    i = j
                else:
                    i += 1
        # The context.py helpers hand a lowering site one of the named sets
        # by a fact computed at the site, so their returns are producers too.
        # The boolean-domain ones are enumerated over their own arity, so a
        # new argument cannot escape the probe; `_decl_slot_forms` keys on
        # the slot TYPE, which has no enumerable domain, and its producers
        # come from RUNNING it over the shape table below -- what the
        # function returns, never a hand-listed tuple beside it.
        for helper in (ctx_mod._slot_lift_forms, ctx_mod._recv_forms,
                       ctx_mod._call_arg_forms):
            arity = len(inspect.signature(helper).parameters)
            for args in itertools.product((False, True), repeat=arity):
                produced |= helper(*args) or frozenset()
        produced |= _decl_slot_forms_produced()
        for form in SinkForm:
            assert form in produced, form
