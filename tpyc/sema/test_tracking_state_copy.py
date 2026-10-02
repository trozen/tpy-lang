"""A per-function state snapshot never clones a parse node or a FunctionInfo.

`SemanticContext.save_function_state` deep-copies `FunctionTrackingState` and
the restore installs that copy, so a cloned node would silently break every
consumer that reaches one by identity -- a `is` compare, or a fact stamped on
a node for a later phase to read off the body's own node. The same holds for
the registry `FunctionInfo`s a call edge points at, which Phase-2 mutation
propagation reads by identity. The containers around them must still be
copies, or a nested def's mutations would leak back into the enclosing scope.

The live scope, namespaces and function node (`LIVE_HANDLE_FIELDS`) come back
as themselves for a second reason: the enclosing analysis goes on binding into
them after the restore, so a copy would take bindings nothing else can see.

The oracle here walks the state itself rather than calling the seeding the
snapshot uses: a route the seeding fails to walk has to show up as a clone,
which it cannot do if both sides of the comparison are collected by it.
"""

import dataclasses
import re
from copy import deepcopy
from enum import Enum

from ..identity_map import IdentityMap, IdentitySet
from ..parse import nodes as N
from ..parse.nodes import is_parse_node
from ..diagnostics import Scope
from ..namespace import Namespace
from ..typesys import (INT32, FunctionInfo, MutationCallEdge,
                       PendingGenericInstanceInfo, RecordInfo, TpyType)
from .alias_rebind import BindKind
from .pending_num import DeferredIntOp
from .context import (BorrowTracker, FunctionTrackingState, PendingLocal,
                      LIVE_HANDLE_FIELDS)


def _populated() -> FunctionTrackingState:
    """A state holding a parse node in every shape the walk has to reach."""
    stmt = N.TpyPassStmt()
    stmt2 = N.TpyPassStmt()
    expr = N.TpyName("x")
    func = N.TpyFunction("f", [], None, [])
    loc = N.SourceLocation(line=3)
    call = N.TpyMethodCall(N.TpyName("obj"), "m", [])
    tracker = BorrowTracker()
    # A node behind a plain attribute of a state object, not in a container.
    tracker.current_stmt = N.TpyPassStmt()
    pre_analyzed: IdentityMap = IdentityMap()
    pre_analyzed[call] = [INT32]
    binds: IdentityMap = IdentityMap()
    binds[N.TpyAssign(N.TpyName("y"), expr)] = BindKind.RVALUE
    gates: IdentitySet = IdentitySet()
    gates.add(N.TpyPassStmt())
    frame_rebinds: IdentityMap = IdentityMap()
    frame_rebinds[N.TpyVarDecl("g", None, None)] = None
    return FunctionTrackingState(
        current_function=func,
        body_root=func,
        compound_stack=[stmt],
        base_init_calls=[(N.TpyMethodCall(N.TpyName("Base"), "__init__", []),
                          "Base.__init__(self, ...)")],
        super_del_call=N.TpyMethodCall(N.TpyName("super"), "__del__", []),
        pending_loop_vars={'i': PendingLocal(INT32, (stmt,), stmt2,
                                             head_first=True)},
        write_history={'x': [(INT32, expr)]},
        nested_def_nodes={'f': N.TpyNestedDef(func)},
        nested_def_block_defs={'f': (stmt, "the 'if' block on line 3")},
        pending_yield_root_checks=[(expr, INT32, loc)],
        pending_generic_yield_sources=[(expr, False)],
        pending_view_storage_checks=[(expr, INT32, loc, INT32, False)],
        unread_coro_locals={'c': stmt},
        var_decl_by_name={'x': N.TpyVarDecl("x", None, None)},
        first_bindings={'w': (N.TpyVarDecl("w", None, None),)},
        pending_return_borrows=[
            {'b': ("return b", [(N.TpyReturn(N.TpyName("b")), None)])}],
        # Fields whose annotation says nothing about what they hold.
        pre_analyzed_method_args=pre_analyzed,
        bind_kinds=binds,
        gate_sites=gates,
        frame_rebind_sites=frame_rebinds,
        frame_binding_nodes={'g': [N.TpyVarDecl("g", None, None)]},
        pass_scoped_frames={'g': N.TpyPassStmt()},
        pending_elem_type_fields=[(N.TpyName("comp"), 'result_elem_type')],
        pending_composite_exprs=[N.TpyName("z")],
        pending_num_deferred=[DeferredIntOp(N.TpyName("p"), (INT32,),
                                            lambda _types: None)],
        pending_num_splices=[N.TpyName("q")],
        arm_decl_sites=[(N.TpyIf(N.TpyName("c"), [], []), "r",
                         N.TpyVarDecl("r", None, N.TpyName("c")))],
        borrow_tracker=tracker,
        current_call_edges=[MutationCallEdge(
            callee_fi=FunctionInfo(name="callee", params=[], return_type=INT32),
            param_map={})],
        current_awaited_subframes=[
            FunctionInfo(name="awaited", params=[], return_type=INT32)],
        # A node and a FunctionInfo behind an ordinary class, which no
        # annotation of a node type announces.
        pending_generic_instances={1: PendingGenericInstanceInfo(
            instance_id=1, variable_name="g",
            record_info=RecordInfo(name="R", fields=[]), record_name="R",
            type_params=[], inferred={},
            expr=N.TpyCall(N.TpyName("R"), []))},
        current_ns=_namespace_with_function(),
        own_ns=_namespace_with_function(),
        current_scope=Scope(namespace=_namespace_with_function()),
    )


def _namespace_with_function() -> Namespace:
    ns = Namespace()
    ns.bind_function(FunctionInfo(name="bound", params=[], return_type=INT32))
    return ns


# Every field of `_populated()` that holds a parse node or a FunctionInfo.
# The equality below turns a new field that could hold one into a test
# failure, so it cannot start being cloned unnoticed.
_FIXTURE_FIELDS = {
    'current_function', 'body_root', 'compound_stack',
    'base_init_calls',
    'super_del_call', 'pending_loop_vars', 'write_history', 'nested_def_nodes',
    'nested_def_block_defs', 'pending_yield_root_checks',
    'pending_generic_yield_sources', 'pending_view_storage_checks',
    'unread_coro_locals', 'var_decl_by_name', 'first_bindings',
    'pending_return_borrows',
    'pre_analyzed_method_args', 'bind_kinds', 'gate_sites',
    'frame_rebind_sites', 'frame_binding_nodes', 'pass_scoped_frames',
    'pending_elem_type_fields', 'pending_composite_exprs', 'arm_decl_sites',
    'pending_num_deferred', 'pending_num_splices',
    'borrow_tracker',
    'current_call_edges', 'current_awaited_subframes',
    'pending_generic_instances', 'current_ns', 'own_ns', 'current_scope',
}

# Classes that cannot hold a parse node or a FunctionInfo, however they are
# nested: a field built only out of these is the one shape the fixture may
# skip. Anything else counts as able to hold one -- including a bare `list`,
# an `object`, and any class not named here -- so a new node-holding field
# fails this guard instead of slipping past it.
_LEAF_VALUE_NAMES = {
    'str', 'int', 'float', 'bool', 'bytes', 'None',
    'SourceLocation',   # a position, the one non-node record nodes carry
    'TpyType',          # deliberately not carried by identity; see the seeder
    'TryTier', 'ValueRange', 'BindingProvenance', '_ModuleInitSentinel',
    'EphemeralKind',
    'LoopClauseEdges',  # sets of names only
    'LoanInfo',         # a borrow kind, a flag and an index key
    'NestedMutationMark',  # a name and four flags
    'OwnSlot',          # a param name and an element index
    'ViewTypeFamily',   # a static descriptor of the str / bytes family
    'InPlaceWrites',    # name -> name sets only
}
_CONTAINER_NAMES = {'list', 'dict', 'set', 'frozenset', 'tuple'}


def _fields_that_can_hold_a_node() -> set[str]:
    """Fields the fixture has to populate -- every one whose annotation is not
    built purely out of leaf value classes. A bare container name with no
    argument (`list`, `IdentityMap`) says nothing about its contents, so it
    is not a leaf either."""
    found: set[str] = set()
    for f in dataclasses.fields(FunctionTrackingState):
        text = str(f.type)
        words = set(re.findall(r"\w+", text))
        bare_container = text.strip() in _CONTAINER_NAMES
        if bare_container or (words - _CONTAINER_NAMES) - _LEAF_VALUE_NAMES:
            found.add(f.name)
    return found


def _reachable_identities(value: object, seen: 'set[int] | None' = None,
                          found: 'dict | None' = None) -> dict:
    """Every parse node and FunctionInfo reachable from `value`, by id.

    Written out in full on purpose: it is the oracle the snapshot's own
    seeding is checked against, so it shares no code with it.
    """
    if seen is None:
        seen = set()
    if found is None:
        found = {}
    if isinstance(value, (str, bytes, bytearray, int, float, complex, bool,
                          type(None), type, Enum)):
        return found
    key = id(value)
    if key in seen:
        return found
    seen.add(key)
    if is_parse_node(value) or isinstance(value, FunctionInfo):
        found[key] = value
    elif isinstance(value, TpyType):
        pass
    elif isinstance(value, IdentityMap):
        for entry_key, entry in value.items():
            _reachable_identities(entry_key, seen, found)
            _reachable_identities(entry, seen, found)
    elif isinstance(value, IdentitySet):
        for member in value:
            _reachable_identities(member, seen, found)
    elif isinstance(value, dict):
        for entry_key, entry in value.items():
            _reachable_identities(entry_key, seen, found)
            _reachable_identities(entry, seen, found)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _reachable_identities(item, seen, found)
    else:
        for item in vars(value).values() if hasattr(value, '__dict__') else ():
            _reachable_identities(item, seen, found)
        for cls in type(value).__mro__:
            for name in getattr(cls, '__slots__', ()):
                _reachable_identities(getattr(value, name, None), seen, found)
    return found


def test_fixture_covers_every_field_that_can_hold_a_node() -> None:
    assert _fields_that_can_hold_a_node() == _FIXTURE_FIELDS
    # Covered in name only is not covered: each field has to REACH a node or
    # a FunctionInfo, which a default-constructed object would not (and which
    # being merely truthy does not say).
    state = _populated()
    for name in sorted(_FIXTURE_FIELDS):
        assert _reachable_identities(getattr(state, name)), name


def test_the_copy_holds_no_cloned_node() -> None:
    state = _populated()
    before = _reachable_identities(vars(state))
    copied = deepcopy(state)
    after = _reachable_identities(vars(copied))
    # Same ids, both states alive: every object the copy holds IS the one the
    # original held.
    assert len(after) == len(before)
    for key, obj in after.items():
        assert key in before, f"{type(obj).__name__} in the copy is a clone"
    # The routes only an object-descending walk reaches.
    assert copied.borrow_tracker.current_stmt is state.borrow_tracker.current_stmt
    assert (copied.current_call_edges[0].callee_fi
            is state.current_call_edges[0].callee_fi)
    assert copied.current_awaited_subframes[0] is state.current_awaited_subframes[0]
    assert (copied.pending_generic_instances[1].expr
            is state.pending_generic_instances[1].expr)
    assert (copied.current_ns.lookup("bound").func_infos[0]
            is state.current_ns.lookup("bound").func_infos[0])


def test_the_live_handles_come_back_as_themselves() -> None:
    state = _populated()
    field_names = {f.name for f in dataclasses.fields(FunctionTrackingState)}
    assert LIVE_HANDLE_FIELDS <= field_names
    copied = deepcopy(state)
    for name in sorted(LIVE_HANDLE_FIELDS):
        live = getattr(state, name)
        # An unset handle would make the identity check below vacuous.
        assert live is not None, name
        assert getattr(copied, name) is live, name


def test_snapshot_still_copies_the_containers() -> None:
    state = _populated()
    copied = deepcopy(state)
    assert copied is not state
    assert copied.compound_stack is not state.compound_stack
    assert copied.nested_def_nodes is not state.nested_def_nodes
    assert copied.nested_def_block_defs is not state.nested_def_block_defs
    assert copied.write_history is not state.write_history
    assert copied.borrow_tracker is not state.borrow_tracker
    # The dict-of-lists is the shape a shallow copy would get wrong: the
    # snapshot must not share the inner list with the live state.
    assert copied.write_history['x'] is not state.write_history['x']
    copied.compound_stack.append(N.TpyPassStmt())
    copied.write_history['x'].append((INT32, N.TpyName("y")))
    assert len(state.compound_stack) == 1
    assert len(state.write_history['x']) == 1
