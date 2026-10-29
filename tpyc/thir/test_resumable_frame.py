"""The published resumable frame's invariants: one failing input per rule
family of `validate_resumable_frame` (blocks and edges, suspensions, case
entries, regions and their edges, handler entries, holders, slot positions,
params, locals), the leaf coverage rule of `validate_resumable_leaves`, and
that the frame builder validates what it publishes."""
from __future__ import annotations

import dataclasses

import pytest

from ..codegen_cpp.context import CodeGenOptions
from ..identity_map import IdentityMap, IdentitySet
from .frame import (
    FrameLoopOwner, HolderRole, LeafRole, THIRAwaitSuspension, THIRExceptRegion, THIRFall,
    THIRFinallyRegion, THIRFrameReturn, THIRLeafSlot, THIRSuspend,
    THIRTryRegion, THIRWithRegion,
)
from .lower import frame as frame_builder
from ..typesys import NominalType, OwnType, TypeParamRef
from .nodes import (
    THIRBorrowedRecord, THIRResumableLeaves, body_header, effective_params,
)
from .testutil import _compile, _entry
from .validate import (
    THIRValidationError, validate_resumable_frame, validate_resumable_leaves,
)


_GEN = ("from typing import Iterator\nfrom tpy import int32\n\n"
        "def g(n: int32) -> Iterator[int32]:\n"
        "    yield n\n    yield n + 1\n\n"
        "def main() -> None:\n"
        "    for x in g(2):\n        print(x)\n"
        "main()\n")

# Every region kind with an edge, a handler, holders of three roles, a
# decomposed loop and a suspending match.
_ASYNC = ("import asyncio\nfrom tpy import int32\n\n\n"
          "class Tracer:\n"
          "    def __enter__(self) -> None:\n        print('enter')\n\n"
          "    def __exit__(self, exc_type, exc_val, exc_tb) -> None:\n"
          "        print('exit')\n\n\n"
          "async def step(n: int32) -> int32:\n    return n\n\n\n"
          "async def f(xs: list[int32], sel: int32) -> int32:\n"
          "    total = 0\n"
          "    try:\n"
          "        for x in xs:\n            total += await step(x)\n"
          "    except ValueError:\n        total = await step(-1)\n"
          "    finally:\n        await step(0)\n"
          "    with Tracer():\n        total += await step(1)\n"
          "    match sel:\n"
          "        case 1:\n            y = await step(2)\n            total += y\n"
          "        case _:\n            pass\n"
          "    return await step(total)\n\n\n"
          "def main() -> None:\n    print(asyncio.run(f([1, 2], 1)))\n\n\n"
          "main()\n")


def _collect(src: str):
    compiler, modules = _compile(src)
    return compiler.collect_thir(_entry(modules), CodeGenOptions(),
                                 tolerate_reject=True)


@pytest.fixture(scope="module")
def gen_ctx():
    return _collect(_GEN)


@pytest.fixture(scope="module")
def async_ctx():
    return _collect(_ASYNC)


def _frame(ctx, name: str):
    return next(f for func, f in ctx.thir_resumable_frames.items()
                if func.name == name)


def _leaves(ctx, name: str) -> THIRResumableLeaves:
    return next(lv for func, lv in ctx.thir_resumables.items()
                if func.name == name)


def _with_block(frame, bid: int, **changes):
    blocks = dict(frame.blocks)
    blocks[bid] = dataclasses.replace(blocks[bid], **changes)
    return dataclasses.replace(frame, blocks=blocks)


def _block_where(frame, pred) -> int:
    return next(bid for bid, b in sorted(frame.blocks.items()) if pred(b))


def _with_suspension(frame, index: int, **changes):
    """`frame` with suspension `index` replaced both as its block's
    terminator and in the suspension list."""
    old = frame.suspensions[index]
    new = dataclasses.replace(old, **changes)
    bid = _block_where(frame, lambda b: b.terminator is old)
    broken = _with_block(frame, bid, terminator=new)
    return dataclasses.replace(broken, suspensions=[
        new if y is old else y for y in frame.suspensions])


def _with_region(frame, kind: type, **changes):
    """`frame` with the first `kind` region of some block's stack changed."""
    bid = _block_where(frame, lambda b: any(isinstance(r, kind)
                                            for r in b.region_stack))
    stack = tuple(dataclasses.replace(r, **changes) if isinstance(r, kind) else r
                  for r in frame.blocks[bid].region_stack)
    return _with_block(frame, bid, region_stack=stack)


def _foreign(holder):
    return dataclasses.replace(holder)


def _first_holder(frame, role: HolderRole):
    tables = (*frame.for_fields, *frame.with_fields, *frame.try_finally_fields)
    return next(h for h in tables if h.role is role)


def _with_first_holder(frame, table: str, **changes):
    """`frame` with the first holder of machinery table `table` changed."""
    first, *rest = getattr(frame, table)
    return dataclasses.replace(
        frame, **{table: (dataclasses.replace(first, **changes), *rest)})


def _await_index(frame) -> int:
    return next(i for i, y in enumerate(frame.suspensions)
                if isinstance(y.payload, THIRAwaitSuspension))


def _return_block(frame) -> int:
    return _block_where(frame, lambda b: isinstance(b.terminator, THIRFrameReturn))


# (case, mutation, the failure it must report). Each mutation breaks exactly
# one rule of an otherwise valid published frame.
_FRAME_RULES = [
    ("entry_block", lambda f: dataclasses.replace(f, entry_block=999),
     "entry block 999 is not a block"),
    ("block_key", lambda f: dataclasses.replace(
        f, blocks={**f.blocks, 999: f.blocks[f.entry_block]}),
     "block 999 is keyed under another id"),
    ("terminator_kind", lambda f: _with_block(f, f.entry_block,
                                              terminator=object()),
     "ends in object"),
    ("terminator_target", lambda f: _with_block(f, f.entry_block,
                                                terminator=THIRFall(next_bb=999)),
     "transfers to missing block 999"),
    ("suspension_payload", lambda f: _with_suspension(f, 0, payload=object()),
     "suspends with object"),
    ("suspension_list", lambda f: dataclasses.replace(
        f, suspensions=f.suspensions[:1]),
     "suspensions are not the frame's suspend terminators"),
    ("suspension_index", lambda f: _with_suspension(f, 0, suspension_index=7),
     "suspension indices are not 0..n-1"),
    ("suspension_resume", lambda f: _with_suspension(
        f, 0, resume_bb=f.suspensions[1].resume_bb),
     "two suspensions resume at one block"),
    ("case_entry_twice", lambda f: dataclasses.replace(
        f, case_entries=f.case_entries + (f.entry_block,)),
     "a case entry is listed twice"),
    ("case_entry_block", lambda f: dataclasses.replace(
        f, case_entries=f.case_entries + (999,)),
     "a case entry is not a block"),
    ("case_entry_resume", lambda f: dataclasses.replace(
        f, case_entries=(f.entry_block,)),
     "the entry or a resume block is no case entry"),
    ("region_kind", lambda f: _with_block(f, f.entry_block,
                                          region_stack=(object(),)),
     "region stack holds object"),
    ("block_stmt_kind", lambda f: _with_block(f, f.entry_block,
                                              stmts=[object()]),
     "holds object"),
    ("block_stmt_role", lambda f: _with_block(
        f, f.entry_block, stmts=[THIRLeafSlot(LeafRole.COND, f.lifted_body[0])]),
     "holds a cond slot"),
    ("params_mirror", lambda f: dataclasses.replace(f, params=()),
     "params do not mirror the source parameters"),
    ("params_capture", lambda f: dataclasses.replace(f, captures=()),
     "a param has no capture kind"),
    ("params_const", lambda f: dataclasses.replace(
        f, const_params=frozenset({"nope"})),
     "a const capture names no param"),
    ("params_capture_kind", lambda f: dataclasses.replace(
        f, captures=tuple(k.value for k in f.captures)),
     "a capture kind is no FrameCaptureKind"),
    ("params_type", lambda f: dataclasses.replace(
        f, params=tuple(dataclasses.replace(p, type=None) for p in f.params)),
     "a param has no type"),
]

_ASYNC_RULES = [
    ("try_finally_entry", lambda f: _with_region(f, THIRTryRegion,
                                                 finally_entry_bb=999),
     "THIRTryRegion of block .* enters missing block 999"),
    ("with_post", lambda f: _with_region(f, THIRWithRegion, post_with_bb=999),
     "THIRWithRegion of block .* enters missing block 999"),
    ("finally_exit", lambda f: _with_region(f, THIRFinallyRegion,
                                            finally_exit_bb=999),
     "THIRFinallyRegion of block .* enters missing block 999"),
    ("except_parent_finally", lambda f: _with_region(
        f, THIRExceptRegion, parent_finally_entry_bb=999),
     "THIRExceptRegion of block .* enters missing block 999"),
    ("handler_region", lambda f: dataclasses.replace(f, handler_entries=IdentityMap(
        [(next(r for b in f.blocks.values() for r in b.region_stack
               if isinstance(r, THIRWithRegion)), {})])),
     "handler entries keyed by THIRWithRegion"),
    ("handler_block", lambda f: dataclasses.replace(f, handler_entries=IdentityMap(
        [(region, IdentityMap((h, 999) for h, _bid in by_handler.items()))
         for region, by_handler in f.handler_entries.items()])),
     "a handler enters missing block 999"),
    ("holder_role", lambda f: dataclasses.replace(
        f, for_fields=(_first_holder(f, HolderRole.WITH_CONTEXT),)),
     "a loop table holds"),
    ("holder_twice", lambda f: dataclasses.replace(
        f, for_fields=f.for_fields + f.for_fields[:1]),
     "is declared twice"),
    ("holder_loop_source", lambda f: dataclasses.replace(
        f, for_src_fields=IdentityMap(
            [(object(), _foreign(_first_holder(f, HolderRole.LOOP)))])),
     "a held loop source names no holder of the frame"),
    ("holder_src_loop", lambda f: dataclasses.replace(
        f, for_src_fields=IdentityMap(
            [(object(), _first_holder(f, HolderRole.LOOP))])),
     "held loop source .* is not its loop's"),
    ("holder_loop_fact", lambda f: _with_first_holder(
        f, "with_fields", drop_runs_user_code=True),
     "with_context holder .* carries a loop fact"),
    ("holder_loop_none", lambda f: _with_first_holder(
        f, "for_fields", loop=None, drop_runs_user_code=False),
     "has no loop and is dropped as running no user code"),
    ("holder_loop_foreign", lambda f: _with_first_holder(
        f, "for_fields", loop=FrameLoopOwner(object(), None, None,
                                             frozenset())),
     "names no loop of the frame"),
    ("holder_sync_ownerless", lambda f: _with_first_holder(
        f, "for_fields", loop=None, drop_runs_user_code=True),
     "synchronous loop holder .* has no loop"),
    ("holder_ownerless_not_async_iter", lambda f: dataclasses.replace(
        f, for_fields=f.for_fields + (dataclasses.replace(
            _first_holder(f, HolderRole.LOOP), name="__for_extra",
            loop=None, drop_runs_user_code=True),)),
     "has no loop and is no `async for` iterator"),
    ("holder_foreign_strategy", lambda f: _with_first_holder(
        f, "for_fields", loop=dataclasses.replace(
            f.for_fields[0].loop,
            info=dataclasses.replace(f.for_fields[0].loop.info))),
     "carries a strategy that is not its loop's"),
    ("holder_not_strategy_field", lambda f: _with_first_holder(
        f, "for_fields", name="__for_extra"),
     "is no field of its loop's strategy"),
    ("holder_foreign_alias", lambda f: _with_first_holder(
        f, "for_fields", loop=dataclasses.replace(
            f.for_fields[0].loop, alias="__for_src_99_t")),
     "names an alias its loop's strategy does not declare"),
    ("holder_manager", lambda f: dataclasses.replace(
        f, with_owned_ctx_map=IdentityMap(
            [(object(), (_foreign(_first_holder(f, HolderRole.WITH_CONTEXT)),))])),
     "a homed manager names no holder of the frame"),
    ("holder_ptr_slot", lambda f: dataclasses.replace(
        f, ptr_slot_map=IdentityMap(
            [(object(), {None: _foreign(_first_holder(f, HolderRole.LOOP))})])),
     "a pointer slot names no holder of the frame"),
    ("holder_bound_handle", lambda f: _with_suspension(
        f, _await_index(f), payload=dataclasses.replace(
            f.suspensions[_await_index(f)].payload,
            prebuilt_slot=dataclasses.replace(f.locals[0]))),
     "a bound-handle await polls no local of the frame"),
    ("slot_role", lambda f: _with_block(
        f, _return_block(f), terminator=THIRFrameReturn(slot=THIRLeafSlot(
            LeafRole.COND, f.blocks[_return_block(f)].terminator.return_stmt))),
     "a return position holds"),
    ("slot_twice", lambda f: _with_block(
        f, _block_where(f, lambda b: b.stmts and b.id != f.entry_block),
        stmts=list(f.blocks[f.entry_block].stmts)),
     "slot sits at two positions"),
    ("local_twice", lambda f: dataclasses.replace(f, locals=f.locals + f.locals[:1]),
     "a frame local is declared twice"),
    ("local_placement", lambda f: dataclasses.replace(f, locals=(
        dataclasses.replace(f.locals[0], placement=None),) + f.locals[1:]),
     "has no placement"),
    ("local_hoisted", lambda f: dataclasses.replace(f, locals=(
        dataclasses.replace(f.locals[0], name="__not_hoisted"),)
        + f.locals[1:]),
     "is no hoisted local of the body"),
    ("local_missing", lambda f: dataclasses.replace(f, locals=f.locals[1:]),
     "hoisted local .* is no frame local"),
]


def test_published_frames_validate(gen_ctx, async_ctx):
    validate_resumable_frame(_frame(gen_ctx, "g"))
    validate_resumable_frame(_frame(async_ctx, "f"))


@pytest.mark.parametrize("case, mutate, message", _FRAME_RULES,
                         ids=[c for c, _m, _w in _FRAME_RULES])
def test_frame_rule_rejects(gen_ctx, case, mutate, message):
    with pytest.raises(THIRValidationError, match=message):
        validate_resumable_frame(mutate(_frame(gen_ctx, "g")))


@pytest.mark.parametrize("case, mutate, message", _ASYNC_RULES,
                         ids=[c for c, _m, _w in _ASYNC_RULES])
def test_async_frame_rule_rejects(async_ctx, case, mutate, message):
    with pytest.raises(THIRValidationError, match=message):
        validate_resumable_frame(mutate(_frame(async_ctx, "f")))


# A loop that does not suspend over a fresh source the frame keeps whole.
_HELD = ("from typing import Iterator\nfrom tpy import int32, Own\n\n\n"
         "class Box:\n"
         "    def __init__(self, n: int32) -> None:\n        self.n = n\n\n\n"
         "def make() -> Own[list[Box]]:\n    return [Box(1), Box(2)]\n\n\n"
         "def g() -> Iterator[int32]:\n"
         "    for b in make():\n        print(b.n)\n"
         "    yield 1\n\n\n"
         "def main() -> None:\n"
         "    for x in g():\n        print(x)\n\n\n"
         "main()\n")


def _with_held_source(frame, **changes):
    """`frame` with its held loop source replaced, in both the loop table
    and the held-source map, by a copy with `changes`."""
    (stmt, old), = ((s, h) for s, h in frame.for_src_fields.items()
                    if h is not None)
    new = dataclasses.replace(old, **changes)
    return dataclasses.replace(
        frame,
        for_fields=tuple(new if h is old else h for h in frame.for_fields),
        for_src_fields=IdentityMap(
            (s, new if h is old else h) for s, h in frame.for_src_fields.items()))


_HELD_RULES = [
    ("held_not_source", lambda f: dataclasses.replace(
        f, for_src_fields=IdentityMap(
            (s, None) for s in f.for_src_fields)),
     "has no strategy and is not its loop's held source"),
    ("held_alias", lambda f: _with_held_source(
        f, loop=dataclasses.replace(
            next(h for h in f.for_src_fields.values() if h is not None).loop,
            alias="__for_src_99_t")),
     "is not spelled as its alias"),
]


@pytest.mark.parametrize("case, mutate, message", _HELD_RULES,
                         ids=[c for c, _m, _w in _HELD_RULES])
def test_held_source_rule_rejects(case, mutate, message):
    frame = _frame(_collect(_HELD), "g")
    validate_resumable_frame(frame)
    with pytest.raises(THIRValidationError, match=message):
        validate_resumable_frame(mutate(frame))


def test_publication_validates_the_frame(monkeypatch):
    # The builder's publication is the frame's validation point: removing
    # the call there must fail this test.
    seen = []

    def record(frame):
        seen.append(frame)
        validate_resumable_frame(frame)
    monkeypatch.setattr(frame_builder, "validate_resumable_frame", record)
    ctx = _collect(_GEN)
    assert [f.func.name for f in seen] == ["g"]
    assert seen[0] is _frame(ctx, "g")


def test_lowered_leaves_cover_every_rendered_slot(async_ctx):
    leaves = _leaves(async_ctx, "f")
    # The `return await` slot renders from the frame alone, so a lowered
    # body with every other slot filled validates.
    ret = leaves.frame.blocks[_return_block(leaves.frame)].terminator
    assert ret.slot not in leaves.leaves
    validate_resumable_leaves("f", leaves, lowered=True)
    # Any other slot left without its leaf is a lowering / printer
    # disagreement.
    missing = next(slot for slot in leaves.frame.slots()
                   if slot.role is LeafRole.AWAIT_ARGS)
    table = IdentityMap((s, lv) for s, lv in leaves.leaves.items()
                        if s is not missing)
    with pytest.raises(THIRValidationError, match="await_args slot has no leaf"):
        validate_resumable_leaves(
            "f", dataclasses.replace(leaves, leaves=table), lowered=True)


_METHODS = ("import asyncio\nfrom typing import Iterator\n"
            "from tpy import int32, readonly\n\n\n"
            "class Bag[T]:\n    hits: int32\n\n"
            "    def __init__(self, item: T) -> None:\n"
            "        self.item = item\n        self.hits = 0\n\n"
            "    async def get(self) -> int32:\n"
            "        self.hits += 1\n        await asyncio.sleep(0)\n        return 1\n\n\n"
            "class Counter:\n"
            "    def __init__(self, n: int32) -> None:\n"
            "        self.n = n\n\n"
            "    @readonly\n"
            "    def count(self) -> Iterator[int32]:\n"
            "        yield self.n\n        yield self.n + 1\n\n\n"
            "def main() -> None:\n"
            "    print(asyncio.run(Bag(3).get()))\n"
            "    for x in Counter(2).count():\n        print(x)\n\n\n"
            "main()\n")


@pytest.fixture(scope="module")
def method_ctx():
    return _collect(_METHODS)


def test_method_frame_receivers(method_ctx):
    # A generic record's receiver is spelled over its own type parameters,
    # which a function body's receiver rule refuses; a `@readonly` method's
    # receiver is read-only.
    get, count = _frame(method_ctx, "get"), _frame(method_ctx, "count")
    assert isinstance(get.receiver, THIRBorrowedRecord)
    assert get.receiver.type.name == "Bag" and not get.receiver.readonly
    assert all(isinstance(a, TypeParamRef) for a in get.receiver.type.type_args)
    assert get.receiver.type.type_args
    assert count.receiver.type.name == "Counter" and count.receiver.readonly
    validate_resumable_frame(get)
    validate_resumable_frame(count)


@pytest.mark.parametrize("name, mutate, message", [
    ("get", lambda f: dataclasses.replace(f, receiver=None),
     "receiver disagrees with the frame's record"),
    ("count", lambda f: dataclasses.replace(f, receiver=dataclasses.replace(
        f.receiver, readonly=False)), "invalid receiver fact"),
    ("get", lambda f: dataclasses.replace(f, receiver=dataclasses.replace(
        f.receiver, type=NominalType("Other"))), "invalid receiver fact"),
    # A frame's receiver is spelled over the record's own type parameters,
    # never at concrete arguments.
    ("get", lambda f: dataclasses.replace(f, receiver=dataclasses.replace(
        f.receiver, type=dataclasses.replace(
            f.receiver.type, type_args=(NominalType("int32"),)))),
     "invalid receiver fact"),
], ids=["missing", "access", "record", "concrete_args"])
def test_method_frame_receiver_rejects(method_ctx, name, mutate, message):
    with pytest.raises(THIRValidationError, match=message):
        validate_resumable_frame(mutate(_frame(method_ctx, name)))


def test_free_frame_takes_no_receiver(gen_ctx, method_ctx):
    stray = _frame(method_ctx, "count").receiver
    with pytest.raises(THIRValidationError,
                       match="receiver disagrees with the frame's record"):
        validate_resumable_frame(
            dataclasses.replace(_frame(gen_ctx, "g"), receiver=stray))


def test_one_header_across_body_roots(method_ctx):
    # Function, constructor and frame headers read through one helper: a
    # method frame binds its receiver first, as a method body does; a
    # constructor binds none and hands back its record.
    leaves = _leaves(method_ctx, "count")
    header = body_header(leaves)
    assert header.name == "count" and header.params is leaves.params
    assert header.receiver is leaves.frame.receiver
    assert header.signature is leaves.signature is not None
    assert body_header(leaves.frame).signature is None
    assert effective_params(leaves)[0].name == "self"
    ctor = next(c for init, c in method_ctx.thir_constructors.items()
                if c.record_name == "Counter")
    ch = body_header(ctor)
    assert ch.name == "Counter.__init__" and ch.receiver is None
    assert ch.signature.return_type == OwnType(ctor.record_layout.type)
    assert effective_params(ctor) == ctor.params
    main = next(fn for fn in method_ctx.thir_functions.values()
                if fn.name == "main")
    fh = body_header(main)
    assert (fh.name, fh.params, fh.receiver, fh.signature) == (
        main.name, main.params, main.receiver, main.signature)


_NARROWED = ("import asyncio\nfrom tpy import int32\n\n\n"
             "async def f(v: int32 | str) -> None:\n"
             "    if isinstance(v, int32):\n"
             "        await asyncio.sleep(0)\n"
             "        print(v + 1)\n\n\n"
             "def main() -> None:\n    asyncio.run(f(1))\n\n\n"
             "main()\n")


def test_resume_extraction_is_published():
    # The resume case's re-extraction of a narrowed union parameter is
    # spelled from facts the leaves carry, not from the printer's scope.
    leaves = _leaves(_collect(_NARROWED), "f")
    sub = leaves.resume_extractions["v"]
    assert not sub.receiver and not sub.ptr_variant and sub.param_const
    assert sub.storage == "v" and not sub.slot_backed
    validate_resumable_leaves("f", leaves, lowered=True)


def test_leaf_verdict_rules_reject(async_ctx):
    leaves = _leaves(async_ctx, "f")
    args = next(s for s in leaves.frame.slots() if s.role is LeafRole.AWAIT_ARGS)
    with pytest.raises(THIRValidationError,
                       match="pointer_sources holds a slot it is not decided for"):
        validate_resumable_leaves("f", dataclasses.replace(
            leaves, pointer_sources=IdentitySet([args])))
    foreign = THIRLeafSlot(LeafRole.FOR_ITERABLE, leaves.frame.lifted_body[0])
    with pytest.raises(THIRValidationError,
                       match="unwrapped_sources holds a slot it is not decided for"):
        validate_resumable_leaves("f", dataclasses.replace(
            leaves, unwrapped_sources=IdentitySet([foreign])))
    with pytest.raises(THIRValidationError,
                       match="resume extractions disagree with the narrowing aliases"):
        validate_resumable_leaves("f", dataclasses.replace(
            leaves, narrow_aliases={"total": "total_narrowed"}))


def test_leaf_params_complete_the_frames(async_ctx):
    # The frame publishes its params once, with names and types; the leaves
    # carry them completed with the binding table's layout facts, and the
    # frame is not changed after it is published.
    leaves = _leaves(async_ctx, "f")
    assert ([(p.name, p.type) for p in leaves.params]
            == [(p.name, p.type) for p in leaves.frame.params])
    assert all(p.passing is not None for p in leaves.params)
    assert all(p.passing is None for p in leaves.frame.params)
    with pytest.raises(THIRValidationError,
                       match="leaf params do not mirror the frame's"):
        validate_resumable_leaves("f", dataclasses.replace(
            leaves, params=leaves.params[1:]), lowered=True)


def test_invalid_resume_extraction_rejects():
    leaves = _leaves(_collect(_NARROWED), "f")
    sub = leaves.resume_extractions["v"]
    for broken in (
            # A free function's frame has no receiver to extract through.
            dataclasses.replace(sub, receiver=True),
            # A receiver reads the frame's `__self`, never a pointer name.
            dataclasses.replace(sub, receiver=True, indirect=True),
            # Const pointees belong to a pointer variant.
            dataclasses.replace(sub, ptr_const=True, ptr_variant=False),
            object()):
        with pytest.raises(THIRValidationError,
                           match="invalid resume extraction for v"):
            validate_resumable_leaves("f", dataclasses.replace(
                leaves, resume_extractions={"v": broken}))


_OWNED_ERASED = ("import asyncio\nfrom tpy import Own\n"
                 "from tpy.coro import Cancellable\n\n\n"
                 "async def one() -> int:\n    return 1\n\n\n"
                 "async def consume(c: Own[Cancellable[int]]) -> int:\n"
                 "    return await c\n\n\n"
                 "def main() -> None:\n"
                 "    print(asyncio.run(consume(one())))\n\n\n"
                 "main()\n")


def test_owned_erased_operand_is_decided_by_the_lowering():
    # An awaited `Own[Cancellable[T]]` handle is an owned erased operand:
    # the lowering records its await-operand slot, and the verdict at a slot
    # of another role is refused.
    leaves = _leaves(_collect(_OWNED_ERASED), "consume")
    (slot,) = leaves.owned_erased_operands
    assert slot.role is LeafRole.AWAIT_OPERAND
    validate_resumable_leaves("consume", leaves, lowered=True)
    other = next(s for s in leaves.frame.slots()
                 if s.role is not LeafRole.AWAIT_OPERAND)
    with pytest.raises(THIRValidationError,
                       match="owned_erased_operands holds a slot it is not decided for"):
        validate_resumable_leaves("consume", dataclasses.replace(
            leaves, owned_erased_operands=IdentitySet([other])))
