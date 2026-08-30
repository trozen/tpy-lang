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
import types

import pytest

from ..compilation_context import _current_compiler, activate_compiler
from ..parse.nodes import TpyIntLiteral, TpyName, TpyStrLiteral
from ..typesys import (CHAR, INT32, STRVIEW, OptionalType, OwnType,
                       RecursiveUnionInfo, TypeParamRef, UnionType)
from .lower import checks
from .lower.arg_table import (NO_CELL, PROLOGUE_CELL, _ArgReq, _ArgRow,
                              _ArgSink, arg_ok, reached, reached_families,
                              register_sink, registered_cells,
                              registered_families)
from .lower import expressions
from .lower.context import _ExprUse, _RecordCtorUse
from .lower.expressions import (_CTOR_ARG_SINK, _CTOR_NESTED_ARG_SINK,
                                _pre_ctor_nested_slot_family)
from .lower.checks import (_CONTAINER_ARG_SINK, _GENERIC_PLAIN_ARG_SINK,
                           _MARKER_NATIVE_ARG_SINK, _MARKER_OWN_ROWS,
                           _MARKER_OWN_SLOT_KINDS,
                           _MARKER_QUALIFIED_ARG_SINK, _MARKER_ROWS,
                           _MARKER_TEMPLATE_ARG_SINK, _NATIVE_ARG_SINK,
                           _PLAIN_ARG_SINK, _PROTOCOL_ARG_SINK,
                           _RECORD_METHOD_ARG_SINK,
                           _VIEW_ARG_SINK, _pre_container_slot_family,
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


class TestViewSinkShape:
    def test_row_order_is_pinned(self):
        # Order is load-bearing: `_witness` fires during the walk, so a
        # reordering changes the recorded face census even when admission
        # is identical. This pin is the tripwire for that.
        assert [r.row for r in _VIEW_ARG_SINK.rows] == [
            "scalar_at_template_slot",
            "protocol_bare_name",
            "str_pass_through",
            "bytes_pass_through",
            "char_pass_through",
            "enum_pass_through",
            "ptr_pass_through",
            "native_iterable_literal",
            "native_iterable_container",
            "native_iterable_field",
            "native_iterable_call",
        ]

    def test_note_tail_is_verbatim(self):
        # probe_sites.py / probe_corpus.py histogram on this string.
        assert _VIEW_ARG_SINK.note == "method.view.arg_shape"

    def test_family_carries_no_own_or_mutated_rows(self):
        # Absence-preserving: the pre-fold ladder had no `own_ok` prefix and
        # passed no `mutated` through, so no Own cell is present and the
        # mutated-slot policy stays off.
        assert not ({r.row for r in _VIEW_ARG_SINK.rows} & _MARKER_OWN_ROWS)
        assert _VIEW_ARG_SINK.mutated_slots is False


class TestProtocolSinkShape:
    def test_row_order_is_pinned(self):
        assert [r.row for r in _PROTOCOL_ARG_SINK.rows] == [
            "shared_pass_through",
            "ru_wrapper_name",
            "optional_ptr",
            "value_record_rvalue",
            "tuple_literal",
        ]

    def test_note_tail_is_verbatim(self):
        assert _PROTOCOL_ARG_SINK.note == "method.protocol.arg_shape"

    def test_optional_ptr_is_the_only_witnessing_cell(self):
        # The face fires from the cell, after the predicate -- the pre-fold
        # ladder's `<pred> and _witness(...)` position.
        assert [(r.row, r.face) for r in _PROTOCOL_ARG_SINK.rows
                if r.face is not None] == [
            ("optional_ptr", "method.protocol_optional_ptr")]

    def test_family_carries_no_own_or_mutated_rows(self):
        assert not ({r.row for r in _PROTOCOL_ARG_SINK.rows}
                    & _MARKER_OWN_ROWS)
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


class TestContainerSinkShape:
    def test_row_order_is_pinned(self):
        assert [r.row for r in _CONTAINER_ARG_SINK.rows] == [
            "scalar_at_template_slot",
            "protocol_bare_name",
            "copy_iter_own_elem",
            "str_pass_through",
            "str_owned_slot",
            "bytes_owned_literal",
            "bytes_view_literal",
            "bytes_owned_slot",
            "bytes_owned_name",
            "bytes_owned_call_rvalue",
            "bytes_pass_through",
            "char_pass_through",
            "enum_pass_through",
            "own_enum_elem",
            "ptr_pass_through",
            "container_pass_through",
            "container_slot_call_rvalue",
            "own_record_rvalue",
            "copy_record_own",
            "own_move",
            "own_lvalue",
            "native_iterable_literal",
            "native_iterable_container",
            "native_iterable_call",
            "own_iter_special",
            "own_container_literal",
            "any_pass_through",
            "container_literal_method",
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
            "own_tuple_call_rvalue",
            "ptr_addr_of_elem",
        ]

    def test_str_owned_slot_precedes_own_lvalue(self):
        # Load-bearing beyond the face census: `str_owned_slot` absorbs the
        # VIEW-form str sources into the inline convert, which is what leaves
        # `Own[str]` as the only position-sensitive payload reaching the
        # unguarded `own_lvalue` cell.
        rows = [r.row for r in _CONTAINER_ARG_SINK.rows]
        assert rows.index("str_owned_slot") < rows.index("own_lvalue")
        # ... and the temp-free MOVE half is decided before it too.
        assert rows.index("own_move") < rows.index("own_lvalue")

    def test_note_tail_is_verbatim(self):
        assert _CONTAINER_ARG_SINK.note == "method.arg_shape"

    def test_witnessing_cells_are_pinned(self):
        assert [(r.row, r.face) for r in _CONTAINER_ARG_SINK.rows
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
        ]

    def test_family_carries_own_rows_and_no_mutated_policy(self):
        assert ({r.row for r in _CONTAINER_ARG_SINK.rows} & _MARKER_OWN_ROWS)
        assert _CONTAINER_ARG_SINK.mutated_slots is False

    def test_own_lvalue_cell_has_no_temps_ok_pre_guard(self):
        # Transcribed as it stands: four sibling ladders gate this row on a
        # flush position and this one does not. It is benign only because of
        # the three neighbours pinned above and beside it -- the prologue's
        # view fence, `str_owned_slot` running first, and the move-source
        # check ahead of the family gate -- which together leave `Own[str]`
        # as the only position-sensitive payload, and that one re-derives its
        # guard in `_lower_call_arg`. So the ABSENCE is the thing under test:
        # adding a guard here is a separate change, and an ablation showed it
        # moves nothing but a recorded reject tag.
        cell = next(r for r in _CONTAINER_ARG_SINK.rows
                    if r.row == "own_lvalue")
        assert cell.extra is None


class TestMarkerSinkSplit:
    """The marker call is THREE families, not one -- the step that turns
    `own_ok` from a re-typed row prefix into a family capability."""

    def test_qualified_row_order_is_pinned(self):
        assert [r.row for r in _MARKER_QUALIFIED_ARG_SINK.rows] == [
            "shared_pass_through",
            "func_ref",
            "lambda",
            "value_opt_callable_pass",
            "none_unit",
            "value_union_temp",
            "own_record_rvalue",
            "own_tparam_call_rvalue",
            "copy_record_own",
            "own_move",
            "own_lvalue",
            "optional_ptr",
            "opt_own_record_name",
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
            "container_field_pass",
            "record_field_marker",
            "value_tuple_field_pass",
            "borrow_ret_record_marker",
            "btuple_literal_marker",
            "same_tparam_name",
            "own_container_literal",
            "native_record_call",
            "protocol_slot",
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
            "own_record_rvalue", "own_tparam_call_rvalue", "copy_record_own",
            "own_move", "own_lvalue", "own_union_ctor",
            "dyn_own_coro_factory", "dyn_own_handle", "dyn_own_forward_call",
            "own_container_literal"})

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
            "value_union_temp", "own_lvalue", "ru_container_literal"]

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
            "value_union_temp",
            "record_rvalue_temp",
            "str_owned_slot",
            "bytes_owned_slot",
            "own_move",
            "own_coerce_cast",
            "own_lvalue",
            "container_literal",
            "ref_param_dictset_literal",
            "own_container_literal",
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
                == {"own_record_rvalue", "copy_record_own",
                    "own_tparam_call_rvalue"})
        assert "record_rvalue_temp" in rows
        assert _PLAIN_ARG_SINK.mutated_slots is False

    def test_the_shared_rows_reach_the_other_families_cells(self):
        # The fold's payoff, stated as a number: these row names are already
        # carried by an earlier-migrated family, and `register_sink` has
        # already proved each reaches the IDENTICAL predicate (it fails the
        # import otherwise). A drop here means a cell stopped being shared.
        others = set()
        for sink in (_VIEW_ARG_SINK, _PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _CONTAINER_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK):
            others |= {r.row for r in sink.rows}
        shared = [r.row for r in _PLAIN_ARG_SINK.rows if r.row in others]
        assert len(shared) == 30
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

    def test_self_this_reaches_the_lambda_row(self):
        # The one per-call input this family adds. It defaults False, which
        # is what every other family spelled, so the lambda cell stays
        # shared instead of forking into a plain-only copy.
        seen = []

        def _spy(req):
            seen.append(req.self_this)
            return False

        sink = _ArgSink(family="t_self", note="x.y",
                        rows=(_ArgRow("t_self1", _spy),))
        assert arg_ok(sink, None, None, {}, None, param_names=frozenset(),
                      narrowed=frozenset(), temps_ok=False,
                      self_this=True) is False
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
        assert len(set(plain) - set(generic)) == 47
        assert {"callable_field", "value_union_temp", "record_rvalue_temp",
                "str_owned_slot", "bytes_owned_slot", "own_coerce_cast",
                "container_literal", "covariant_temp", "union_pass_through",
                "value_opt_pass_through", "deref_coerce",
                "record_field_ref"} <= (set(plain) - set(generic))

    def test_the_shared_rows_reach_the_other_families_cells(self):
        # `register_sink` has already proved each of these reaches the
        # IDENTICAL predicate (it fails the import otherwise). The six that
        # are NOT shared are the generic-only spellings: the substituted
        # borrow-tuple pair, the Own[protocol] container-conformer cell, the
        # literal-only Own[str] cell (plain's `str_owned_slot` also takes
        # view-form names), and the peeled list literals (plain's
        # `container_literal` does not peel).
        others = set()
        for sink in (_VIEW_ARG_SINK, _PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _CONTAINER_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK,
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
        assert len([r for r in rows if r in others]) == 24

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

    def test_a_bare_type_param_slot_flush_gates_a_scalar_literal(self):
        # The ref-slot temp needs a statement to flush into, so the same
        # literal admits only in a flush position.
        def _at(temps_ok):
            return _pre_generic_slot_family(
                _ArgReq(TpyIntLiteral(1), INT32, {}, None, frozenset(),
                        frozenset(), False, temps_ok,
                        open_ptype=TypeParamRef("T")))

        assert _at(True) is True
        assert _at(False) is False

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

class TestRecordMethodSinkShape:
    """The user-record method family -- the widest in the fold at 67 rows.

    The design predicted "needs `overload`/`index` in ctx"; it needs a THIRD
    per-call fact too (`frame_capturing`, the generator/coro-factory flag).
    It is also the only family so far with NO prologue: one flat `or`-chain,
    transcribed row for row.
    """

    def test_row_order_is_pinned(self):
        assert [r.row for r in _RECORD_METHOD_ARG_SINK.rows] == [
            "lambda",
            "func_ref",
            "callable_value_pass",
            "callable_object",
            "plain_scalar_slot",
            "tparam_scalar",
            "tparam_open_pass",
            "float_literal_pass_through",
            "int_literal_bigint",
            "str_pass_through",
            "bytes_pass_through",
            "char_pass_through",
            "enum_pass_through",
            "ptr_pass_through",
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
            "own_record_rvalue",
            "own_tparam_call_rvalue",
            "copy_record_own",
            "own_move",
            "own_lvalue",
            "opt_own_record_name",
            "own_optional_record_rvalue",
            "own_opt_slot",
            "union_member_lift_none",
            "optional_ptr_no_temp",
            "container_pass_through",
            "container_field_pass",
            "record_pass_through",
            "record_field_marker",
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
            "value_opt_scalar_value",
            "str_literal_value_opt",
            "bytes_literal_value_opt",
            "none_value_opt",
            "none_unit",
            "own_container_literal",
            "optional_ptr_container",
            "optional_ptr_container_literal",
            "optional_ptr_scalar_temp",
            "protocol_bare_name",
            "protocol_slot",
            "container_literal_method",
            "ru_wrapper_name",
            "ru_wrapper_member_name",
            "ru_wrapper_scalar_literal",
            "ru_wrapper_member_rvalue",
            "borrow_ret_record_marker",
        ]

    def test_note_tail_is_verbatim(self):
        # Shared verbatim with the container family, which is correct: the
        # AST method loop spells one tag for both receiver kinds and
        # `probe_sites.py` histograms it.
        assert _RECORD_METHOD_ARG_SINK.note == "method.arg_shape"

    def test_it_has_no_prologue(self):
        # The only family in the fold whose ladder was a single flat
        # `or`-chain: nothing decides ahead of the rows.
        assert _RECORD_METHOD_ARG_SINK.pre is None

    def test_witnessing_cells_are_pinned(self):
        assert [(r.row, r.face) for r in _RECORD_METHOD_ARG_SINK.rows
                if r.face is not None] == [
            ("tparam_scalar", "method.tparam_scalar_arg"),
            ("tparam_open_pass", "method.tparam_open_pass_arg"),
            ("nullable_proto_addr", "arg.nullable_proto_addr"),
            ("union_pass_deep_const", "method.union_pass_arg"),
            ("bytes_literal_value_opt", "method.bytes_literal_value_opt"),
        ]

    def test_flush_gated_cells_are_pinned(self):
        assert [r.row for r in _RECORD_METHOD_ARG_SINK.rows
                if r.extra is not None] == [
            "own_lvalue",
            "record_rvalue_temp_factory",
            "tparam_slot_temp",
            "union_ctor_temp",
            "union_bytes_literal_temp",
            "value_union_temp",
            "optional_ptr_container_literal",
            "optional_ptr_scalar_temp",
            "ru_wrapper_member_name",
            "ru_wrapper_scalar_literal",
            "ru_wrapper_member_rvalue",
        ]

    def test_the_factory_cell_gates_on_both_facts(self):
        # `temps_ok and frame_capturing and` -- the one cell in the fold
        # whose pre-guard is a conjunction, and the only reader of
        # `frame_capturing`. Both halves must be required.
        cell = next(r for r in _RECORD_METHOD_ARG_SINK.rows
                    if r.row == "record_rvalue_temp_factory")

        def _req(temps_ok, frame):
            return _ArgReq(None, None, {}, None, frozenset(), frozenset(),
                           False, temps_ok, index=0,
                           frame_capturing=frame)

        assert cell.extra(_req(True, True)) is True
        assert cell.extra(_req(True, False)) is False
        assert cell.extra(_req(False, True)) is False

    def test_family_carries_no_mutated_policy(self):
        # Absence-preserving: this ladder has `overload.mutated_params` in
        # hand and never consults it, so the flag stays off. Turning it on
        # moves which bodies route, so it is its own change.
        assert _RECORD_METHOD_ARG_SINK.mutated_slots is False

    def test_the_shared_rows_reach_the_other_families_cells(self):
        # `register_sink` has already proved each shared name reaches the
        # IDENTICAL predicate (it fails the import otherwise). This pins the
        # SPLIT: 40 of 69 rows were already written for another family.
        others = set()
        for sink in (_VIEW_ARG_SINK, _PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _CONTAINER_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK,
                     _PLAIN_ARG_SINK, _GENERIC_PLAIN_ARG_SINK):
            others |= {r.row for r in sink.rows}
        rows = [r.row for r in _RECORD_METHOD_ARG_SINK.rows]
        assert len([r for r in rows if r in others]) == 40
        assert [r for r in rows if r not in others] == [
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
            "optional_ptr_container",
            "optional_ptr_container_literal",
            "optional_ptr_scalar_temp",
        ]

    def test_the_lookalike_cells_are_not_the_shared_ones(self):
        # Five names deliberately shadow a shared row because the predicate
        # is DIFFERENT, and `register_sink` would have rejected reusing the
        # shared name. Pinned so a later step cannot quietly collapse them:
        # collapsing any pair is a behaviour change, not a rename.
        rows = {r.row: r.fn for r in _RECORD_METHOD_ARG_SINK.rows}
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
        # Absence-preserving. This family is NOT a subset of plain (it
        # carries 41 cells plain has no name for), but 43 of plain's are
        # simply missing -- holes for the post-fold pass, named so a later
        # step cannot fill one and call it a transcription.
        plain = {r.row for r in _PLAIN_ARG_SINK.rows}
        mine = {r.row for r in _RECORD_METHOD_ARG_SINK.rows}
        assert len(plain - mine) == 43
        assert {"shared_pass_through", "callable_field", "str_owned_slot",
                "bytes_owned_slot", "own_coerce_cast", "container_literal",
                "ref_param_dictset_literal", "covariant_temp",
                "readonly_record_ctor", "required_protocol_union",
                "union_coerced_literal", "own_union_ctor",
                "own_union_call_pass", "dyn_own_coro_factory",
                "dyn_own_handle", "dyn_own_forward_call",
                "wide_opt_deref_name", "opt_view_identity_coerce",
                "list_repeat_proto", "tuple_literal_value_opt",
                "own_tuple_storage_elem", "wrapper_ref_tuple_elem",
                "record_borrow_call", "opt_own_record_rvalue",
                "opt_string_literal", "recursive_union_borrow_call",
                "record_elem_subscript", "container_comp",
                "borrow_tuple_field", "borrow_tuple_subscript",
                "record_field_ref", "deref_coerce",
                "readonly_container_rvalue", "ru_container_literal",
                "ru_wrapper_borrow_call", "ru_wrapper_value_call",
                "ru_wrapper_field", "ru_wrapper_own_call"} <= (plain - mine)

    def test_it_does_not_open_with_the_shared_pass_through_fold(self):
        # The ladder spells that fold's members out one by one, in its own
        # order, and without four of them -- so the cells are separate here
        # and `shared_pass_through` is genuinely absent, not renamed.
        mine = {r.row for r in _RECORD_METHOD_ARG_SINK.rows}
        assert "shared_pass_through" not in mine
        assert {"str_pass_through", "bytes_pass_through", "char_pass_through",
                "enum_pass_through", "ptr_pass_through",
                "value_tuple_pass_through", "span_coerce",
                "slice_ctor_pass_through", "own_scalar_rvalue",
                "own_record_rvalue", "container_pass_through",
                "record_pass_through", "float_literal_pass_through",
                "int_literal_bigint"} <= mine


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
        assert _CONTAINER_ARG_SINK.pre is not None

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
    admit -- `own_lvalue` is safe without a flush guard precisely because the
    view leg runs first. A synthetic `pre=lambda: False` proves the walk
    consults a prologue; only this proves THIS prologue still decides what it
    decided.
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
            "own_move",
            "own_lvalue",
            "own_move_source_slice",
            "own_record_rvalue",
            "own_tparam_call_rvalue",
            "own_opt_ptr_name_move",
            "opt_own_ptr_opt_name_move",
            "opt_own_record_name",
            "opt_own_container_name",
            "copy_record_own",
            "copy_open_elem",
            "generic_open_slot_elem",
            "func_ref",
            "async_factory_wrap",
            "callable_value_pass",
            "field_read_ref_ctor",
            "container_literal",
            "own_container_literal",
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
            "own_optional_record_rvalue",
            "value_record_rvalue",
            "value_union_temp",
            "tuple_literal",
            "lambda",
            "optional_ptr",
            "protocol_slot_ctor",
            "dyn_own_conformer",
            "record_rvalue_temp_ctor",
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
        for sink in (_VIEW_ARG_SINK, _PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _CONTAINER_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK,
                     _MARKER_NATIVE_ARG_SINK, _MARKER_TEMPLATE_ARG_SINK,
                     _PLAIN_ARG_SINK, _GENERIC_PLAIN_ARG_SINK,
                     _RECORD_METHOD_ARG_SINK):
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
            ("container_literal", "_x_not_mutated"),
            ("union_ctor_temp", "_x_temps_ok"),
            ("protocol_union_literal_temp", "_x_temps_ok"),
            ("value_union_temp", "_x_temps_ok"),
        ]
        assert [(r.row, r.extra.__name__) for r in _CTOR_NESTED_ARG_SINK.rows
                if r.extra is not None] == [
            ("const_rvalue", "_x_ctor_const_rvalue_slot"),
            ("str_pass_through_unmutated", "_x_ctor_str_source_allowed"),
        ]

    def test_one_decisive_cell_in_the_whole_table(self):
        # The nested tail's un-mutated record-rvalue slot block, the only
        # cell in the fold that REJECTS at a POSITION rather than falling
        # through. Every other family expresses its rejects in a prologue.
        decisive = [(s.family, r.row)
                    for s in (_CTOR_ARG_SINK, _CTOR_NESTED_ARG_SINK,
                              _VIEW_ARG_SINK, _PROTOCOL_ARG_SINK,
                              _NATIVE_ARG_SINK, _CONTAINER_ARG_SINK,
                              _MARKER_QUALIFIED_ARG_SINK,
                              _MARKER_NATIVE_ARG_SINK,
                              _MARKER_TEMPLATE_ARG_SINK, _PLAIN_ARG_SINK,
                              _GENERIC_PLAIN_ARG_SINK,
                              _RECORD_METHOD_ARG_SINK)
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
        # Three nested-only cells, all of which the direct family answers
        # elsewhere: `const_rvalue` is the decisive slot block (direct
        # spells the same slot as the flush-gated `record_rvalue_temp_ctor`
        # at the very end of its tuple), `str_pass_through_unmutated`
        # hardcodes `mutated=False` where direct threads the fact, and
        # `ptr_pass_through` is one leg of the direct family's
        # `shared_pass_through` bundle, taken alone because the rest of that
        # bundle is not temp-free.
        assert nested - direct == {"const_rvalue",
                                   "str_pass_through_unmutated",
                                   "ptr_pass_through"}

    def test_the_direct_cells_absent_from_nested_are_named_and_counted(self):
        # Absence-preserving: the flush-less nested position admits only
        # temp-FREE renders, so 46 direct cells are simply absent. The count
        # is what closes the gap -- naming a subset leaves the unnamed
        # absences free to be filled silently later.
        direct = {r.row for r in _CTOR_ARG_SINK.rows}
        nested = {r.row for r in _CTOR_NESTED_ARG_SINK.rows}
        assert len(direct - nested) == 46
        assert {"mutated_container_literal", "str_pass_through",
                "shared_pass_through", "own_lvalue", "own_bytes_literal",
                "container_literal", "own_container_literal",
                "union_ctor_temp", "union_member_lift", "value_union_temp",
                "optional_ptr", "protocol_slot_ctor", "tuple_literal",
                "lambda", "func_ref", "record_rvalue_temp_ctor",
                "protocol_union", "protocol_union_literal_temp",
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
        for sink in (_VIEW_ARG_SINK, _PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _CONTAINER_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK,
                     _PLAIN_ARG_SINK, _GENERIC_PLAIN_ARG_SINK,
                     _RECORD_METHOD_ARG_SINK):
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
            "own_opt_ptr_name_move",
            "opt_own_ptr_opt_name_move",
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
            "protocol_slot_ctor",
            "record_rvalue_temp_ctor",
        ]
        assert len([r for r in rows if r in others]) == 31

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
                                        _RECORD_METHOD_ARG_SINK,
                                        _CONTAINER_ARG_SINK,
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
        for sink in (_VIEW_ARG_SINK, _PROTOCOL_ARG_SINK, _NATIVE_ARG_SINK,
                     _CONTAINER_ARG_SINK, _MARKER_TEMPLATE_ARG_SINK,
                     _MARKER_NATIVE_ARG_SINK, _MARKER_QUALIFIED_ARG_SINK,
                     _PLAIN_ARG_SINK, _GENERIC_PLAIN_ARG_SINK,
                     _RECORD_METHOD_ARG_SINK, _CTOR_ARG_SINK,
                     _CTOR_NESTED_ARG_SINK):
            assert sink.family in families
            cells = registered_cells()
            for row in sink.rows:
                assert (sink.family, row.row) in cells
