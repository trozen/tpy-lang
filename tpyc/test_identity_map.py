"""IdentityMap / IdentitySet: the containers that let a side table key on an
object without letting its address be recycled underneath the entry."""

import ast
import copy
import dataclasses
import gc
import weakref
from pathlib import Path

import pytest

from tpyc.identity_map import IdentityMap, IdentitySet
from tpyc.thir.nodes import THIRResumableBody


class _Node:
    """Stands in for an AST node: a plain object, compared by identity."""

    def __init__(self, tag: str = "") -> None:
        self.tag = tag


class _Unhashable:
    """The shape the compiler actually stores: `@dataclass`-like, so `__eq__`
    is defined and `__hash__` is None -- unusable as a dict key."""

    __hash__ = None

    def __eq__(self, other) -> bool:
        return True


class TestKeysStayAlive:
    """The invariant the whole module exists for: an entry outliving its key
    is what lets a later object at the recycled address inherit the fact."""

    def test_the_map_keeps_its_key_alive(self):
        m: IdentityMap = IdentityMap()
        node = _Node()
        ref = weakref.ref(node)
        m[node] = "fact"
        del node
        gc.collect()
        assert ref() is not None
        assert m[ref()] == "fact"

    def test_a_plain_id_keyed_dict_does_not(self):
        # The control: the same table shape without the ownership, which is
        # what every converted site used to be.
        d: dict = {}
        node = _Node()
        ref = weakref.ref(node)
        d[id(node)] = "fact"
        del node
        gc.collect()
        assert ref() is None
        # ... and the entry is still there, answering for a dead address.
        assert len(d) == 1

    def test_the_set_keeps_its_member_alive(self):
        s: IdentitySet = IdentitySet()
        node = _Node()
        ref = weakref.ref(node)
        s.add(node)
        del node
        gc.collect()
        assert ref() is not None

    def test_a_deleted_entry_releases_the_key(self):
        m: IdentityMap = IdentityMap()
        node = _Node()
        ref = weakref.ref(node)
        m[node] = 1
        del m[node]
        del node
        gc.collect()
        assert ref() is None

    def test_a_discarded_member_releases_the_key(self):
        s: IdentitySet = IdentitySet()
        node = _Node()
        ref = weakref.ref(node)
        s.add(node)
        s.discard(node)
        del node
        gc.collect()
        assert ref() is None


class TestIdentityNotEquality:
    def test_two_equal_but_distinct_keys_do_not_collide(self):
        m: IdentityMap = IdentityMap()
        a, b = _Unhashable(), _Unhashable()
        assert a == b
        m[a] = "a"
        m[b] = "b"
        assert m[a] == "a"
        assert m[b] == "b"
        assert len(m) == 2

    def test_unhashable_keys_are_accepted(self):
        # A plain dict cannot hold these at all -- the reason `id()` keys were
        # reached for in the first place.
        s: IdentitySet = IdentitySet()
        item = _Unhashable()
        s.add(item)
        assert item in s
        with pytest.raises(TypeError):
            {item: 1}


class TestMapSurface:
    def test_get_setdefault_pop_and_contains(self):
        m: IdentityMap = IdentityMap()
        a, b = _Node("a"), _Node("b")
        assert m.get(a) is None
        assert m.get(a, "dflt") == "dflt"
        assert m.setdefault(a, 1) == 1
        assert m.setdefault(a, 2) == 1
        assert a in m and b not in m
        assert m.pop(a) == 1
        assert m.pop(a, "gone") == "gone"
        with pytest.raises(KeyError):
            m.pop(a)

    def test_iteration_yields_keys_and_items_pairs(self):
        m: IdentityMap = IdentityMap()
        a, b = _Node("a"), _Node("b")
        m[a] = 1
        m[b] = 2
        assert [k.tag for k in m] == ["a", "b"]
        assert sorted(m.values()) == [1, 2]
        assert [(k.tag, v) for k, v in m.items()] == [("a", 1), ("b", 2)]

    def test_missing_key_raises(self):
        m: IdentityMap = IdentityMap()
        with pytest.raises(KeyError):
            m[_Node()]

    def test_empty_is_falsy(self):
        m: IdentityMap = IdentityMap()
        assert not m
        m[_Node()] = 1
        assert m


class TestSnapshotRestore:
    """`SemanticContext.try_analysis` saves the type cache, runs a speculative
    analysis, then restores -- the copy must not alias the live table."""

    def test_copy_clear_update_round_trip(self):
        live: IdentityMap = IdentityMap()
        a, b = _Node("a"), _Node("b")
        live[a] = "before"
        saved = live.copy()

        live[a] = "trial"
        live[b] = "trial-only"

        live.clear()
        live.update(saved)

        assert live[a] == "before"
        assert b not in live
        assert len(live) == 1

    def test_a_copy_does_not_alias_the_original(self):
        live: IdentityMap = IdentityMap()
        a = _Node("a")
        live[a] = 1
        snap = live.copy()
        live[a] = 2
        assert snap[a] == 1

    def test_a_snapshot_keeps_its_keys_alive(self):
        live: IdentityMap = IdentityMap()
        node = _Node()
        ref = weakref.ref(node)
        live[node] = 1
        saved = live.copy()
        live.clear()
        del node
        gc.collect()
        assert ref() is not None
        assert len(saved) == 1


class TestSetSurface:
    def test_ior_merges_another_identity_set(self):
        # `ctx.all_last_uses |= analyze_last_uses(...)` is the shape.
        acc: IdentitySet = IdentitySet()
        a, b = _Node("a"), _Node("b")
        produced: IdentitySet = IdentitySet([a, b])
        acc |= produced
        assert a in acc and b in acc
        assert len(acc) == 2

    def test_ior_merges_a_plain_iterable(self):
        acc: IdentitySet = IdentitySet()
        nodes = [_Node("a"), _Node("b")]
        acc |= nodes
        assert all(n in acc for n in nodes)

    def test_ior_keeps_the_merged_members_alive(self):
        acc: IdentitySet = IdentitySet()
        node = _Node()
        ref = weakref.ref(node)
        acc |= IdentitySet([node])
        del node
        gc.collect()
        assert ref() is not None

    def test_add_is_idempotent_and_remove_raises_when_absent(self):
        s: IdentitySet = IdentitySet()
        a = _Node("a")
        s.add(a)
        s.add(a)
        assert len(s) == 1
        s.remove(a)
        with pytest.raises(KeyError):
            s.remove(a)
        s.discard(a)  # discard of an absent member is quiet

    def test_iteration_yields_the_members(self):
        a, b = _Node("a"), _Node("b")
        s: IdentitySet = IdentitySet([a, b])
        assert [n.tag for n in s] == ["a", "b"]
        assert not IdentitySet()


class TestDataclassDefaults:
    """A field TYPED as an identity container but DEFAULTED to `dict()` /
    `set()` hands a plain, non-owning table to every constructor call that
    omits it -- the exact hazard these containers remove, reintroduced where
    the annotation says it cannot happen."""

    # Walked as an AST, not matched as text: a wrapped default
    # (`x: \'IdentityMap\' = (\\n    field(default_factory=dict))`) is the same
    # defect and a line-oriented pattern would not see it.
    _CONTAINERS = ("IdentityMap", "IdentitySet")

    @staticmethod
    def _annotated_container(node: ast.AnnAssign) -> "str | None":
        """The container this field is annotated as, through the string form."""
        ann = node.annotation
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                return None
        name = getattr(ann, "id", None)
        return name if name in TestDataclassDefaults._CONTAINERS else None

    @staticmethod
    def _default_factory(node: ast.AnnAssign) -> "str | None":
        """The `default_factory=` argument of a `field(...)` default, if any."""
        call = node.value
        while isinstance(call, (ast.Starred,)):
            call = call.value
        if not (isinstance(call, ast.Call)
                and getattr(call.func, "id", None) == "field"):
            return None
        for kw in call.keywords:
            if kw.arg == "default_factory":
                return getattr(kw.value, "id", None) or "<expression>"
        return None

    def test_every_annotated_field_defaults_to_the_matching_container(self):
        root = Path(__file__).resolve().parent
        offenders = []
        for path in sorted(root.rglob("*.py")):
            if path.name.startswith("test_") or path.name == "identity_map.py":
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.AnnAssign):
                    continue
                annotated = self._annotated_container(node)
                if annotated is None:
                    continue
                factory = self._default_factory(node)
                if factory is not None and factory != annotated:
                    offenders.append(
                        f"{path.relative_to(root)}:{node.lineno} "
                        f"{annotated} defaulted to {factory}")
        assert not offenders, "\n".join(offenders)

    def test_a_resumable_body_built_from_defaults_owns_every_map(self):
        # The seam maps are optional constructor arguments, so a test or a
        # future lowering arm that omits one gets the default -- it must be an
        # IdentityMap, not a dict.
        body = THIRResumableBody(
            leaves=IdentityMap(), conds=IdentityMap(),
            await_args=IdentityMap(), return_values=IdentityMap())
        for f in dataclasses.fields(body):
            # Forward-referenced annotations keep their quotes in `f.type`.
            if f.type.strip("'\"") == "IdentityMap":
                assert isinstance(getattr(body, f.name), IdentityMap), f.name
            else:
                # A name-keyed table is the one shape that may be a dict;
                # anything else keyed by a node must say IdentityMap.
                assert f.type.strip("'\"").startswith("Mapping[str"), f.name


class TestDeepCopy:
    """`SemanticContext.save_function_state` deep-copies the whole per-function
    state and the RESTORE installs that copy, so a cloned key would leave every
    later lookup -- made with the live AST node -- missing."""

    def test_a_deep_copy_keeps_its_keys_by_identity(self):
        m: IdentityMap = IdentityMap()
        node = _Node("a")
        m[node] = ["fact"]
        clone = copy.deepcopy(m)
        assert node in clone
        assert next(iter(clone)) is node

    def test_a_deep_copy_still_isolates_the_values(self):
        m: IdentityMap = IdentityMap()
        node = _Node("a")
        m[node] = ["fact"]
        clone = copy.deepcopy(m)
        clone[node].append("trial")
        assert m[node] == ["fact"]

    def test_a_deep_copied_set_keeps_its_members(self):
        node = _Node("a")
        s: IdentitySet = IdentitySet([node])
        assert node in copy.deepcopy(s)

    def test_a_deep_copy_nested_in_a_dataclass_keeps_its_keys(self):
        # The real shape: the container is a field of the object being copied.
        @dataclasses.dataclass
        class _State:
            table: IdentityMap = dataclasses.field(default_factory=IdentityMap)

        node = _Node("a")
        state = _State()
        state.table[node] = 1
        assert node in copy.deepcopy(state).table
