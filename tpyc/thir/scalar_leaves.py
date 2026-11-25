"""The scalar leaves and records THIR storage facts and MIR model."""

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import Enum, auto

from ..type_def_registry import (
    NativeMembers, float_traits_of, int_traits_of, is_borrowing_view_type, type_def_of, zero_value_of,
)
from ..typesys import (
    readonly_elements,
    Loan, NominalType, OptionalType, OwnType, ReadonlyType, RecordInfo, Representation, TpyType, TupleType,
    TypeParamRef, TypeRegistry, UnionType, is_void_like_type,
    is_inert_leaf, loan_class, is_owned_leaf, is_primitive_owned_leaf, record_owner, unwrap_readonly, unwrap_ref_type,
    view_family_of, view_owned_leaf,
)


def storage_leaf(typ: object, representation: Representation = Representation.STORAGE) -> bool:
    """Whether `typ` is a scalar leaf at `representation`: a non-generic
    nominal type the loan classifier (`typesys.loan_class`) proves inert
    there -- the primitives by `TypeDef.loan_inert`, and enum values. A
    wrapper (readonly, Own, Literal) or aggregate is never a leaf itself."""
    return isinstance(typ, NominalType) and not typ.type_args and is_inert_leaf(typ, representation)


def owned_leaf(typ: object) -> bool:
    """Whether `typ` is an owned leaf: a non-generic nominal type whose
    TypeDef declares `owned_leaf` (`typesys.is_owned_leaf`). MIR holds one as
    owned storage or as a readonly borrow of storage, never as a scalar."""
    return isinstance(typ, NominalType) and not typ.type_args and is_owned_leaf(typ)


def primitive_owned_leaf(typ: object) -> bool:
    """An owned leaf whose TypeDef carries the primitive-operation contract
    (`typesys.is_primitive_owned_leaf`): a certified operation reads it
    through a borrow, and the runtime prints it."""
    return owned_leaf(typ) and is_primitive_owned_leaf(typ)


def view_leaf(typ: object) -> bool:
    """Whether `typ` is a borrowing view over an owned leaf
    (`typesys.view_owned_leaf`: `StrView`, `BytesView`). MIR holds one as a
    readonly borrowed holder typed by the view, whose referents are the
    owned-leaf storage it reads; a Span or dict view is no such leaf."""
    return isinstance(typ, NominalType) and not typ.type_args and view_owned_leaf(typ) is not None


def holds_loan(typ: object) -> bool:
    """Whether a value of `typ` held in storage holds a borrow
    (`typesys.loan_class`): a view, or a record, container or wrapper with
    a view somewhere inside. UNKNOWN is not YES: MIR keeps an unproved
    member away from a stored loan only where a record has a view member
    (`loan_free` beside it, `definitions._record_members`)."""
    return isinstance(typ, TpyType) and loan_class(typ).holds is Loan.YES


def loan_free(typ: object) -> bool:
    """Whether a value of `typ` is proved to hold no borrow: NO, never
    UNKNOWN (a native, protocol or `bytearray` member the classifier cannot
    decide)."""
    return isinstance(typ, TpyType) and loan_class(typ).holds is Loan.NO


def view_compatible(holder: object, source: object) -> bool:
    """Whether a view holder of type `holder` may hold a borrow of a
    `source` value: `holder` is its family's view type (`view_leaf`) and
    `source` is a member of the same family that is an owned leaf (the
    family's owned type or an owned sibling such as `String`; `bytearray`
    is a reference type and stays out) or the family's view type itself.

    A container view (`container_view`: a Span, a dict view) holds a borrow
    of a native container's elements or of another view of its own kind
    over them: keyed on the type arguments, which name the elements both
    sides store. A mutable view never views readonly elements."""
    if container_view(holder):
        if not isinstance(source, NominalType) or _element_arguments(source) != _element_arguments(holder):
            return False
        if container_view(source):
            return (source.qualified_name() == holder.qualified_name()
                    and (readonly_elements(holder) or not readonly_elements(source)))
        return native_container_type(source)
    if not view_leaf(holder) or not isinstance(source, NominalType):
        return False
    family = view_family_of(holder)
    return view_family_of(source) is family and (owned_leaf(source) or source == family.view_type)


def view_endpoint(holder: object, member: object) -> bool:
    """Whether a view result of type `holder` may be a borrow ending at a
    record member of type `member`: an owned-leaf member it views, or the
    loan a view member stores (`view_compatible`)."""
    return (owned_leaf(member) or view_leaf(member)) and view_compatible(holder, member)


def container_view(typ: object) -> bool:
    """A borrowing view with no owned-leaf family (`is_borrowing_view`, and
    no `view_owned_leaf`): a Span or a dict view, whose loan is on a native
    container's elements rather than on an owned leaf's buffer."""
    return (isinstance(typ, NominalType) and bool(typ.type_args) and is_borrowing_view_type(typ)
            and view_owned_leaf(typ) is None)


def native_container_type(typ: object) -> bool:
    """A native container: a nominal type whose TypeDef declares it owns
    the values of its type arguments as elements (`TypeDef.owns_elements`).
    Which elements MIR admits is the element rule's (`container_element`)."""
    if not isinstance(typ, NominalType) or not typ.type_args:
        return False
    td = type_def_of(typ)
    return td is not None and td.owns_elements


def native_container_subject(typ: TpyType) -> TpyType:
    """The container a value of `typ` is: seen through the reference and
    access wrappers, and through the ownership wrapper of an owned
    container (`Own[list[T]]` holds a `list[T]`)."""
    typ = unwrap_readonly(unwrap_ref_type(typ))
    while isinstance(typ, OwnType):
        typ = unwrap_readonly(unwrap_ref_type(typ.wrapped))
    return typ


def holds_elements(typ: TpyType) -> bool:
    """Whether a value of `typ` reaches container elements: a native
    container or a container view (not a tuple, not a string)."""
    subject = native_container_subject(typ)
    return native_container_type(subject) or container_view(subject)


def _element_arguments(typ: NominalType) -> tuple[TpyType, ...]:
    return tuple(unwrap_readonly(a) for a in typ.type_args if isinstance(a, TpyType))


def _native_members(typ: object) -> NativeMembers | None:
    if not isinstance(typ, NominalType) or not (native_container_type(typ) or container_view(typ)):
        return None
    td = type_def_of(typ)
    members = td.native_members if td is not None else None
    args = typ.type_args
    if (members is None or td.param_kinds and len(args) != len(td.param_kinds)
            or any(i is not None and (i >= len(args) or not isinstance(args[i], TpyType))
                   for i in (members.element, members.value))):
        return None
    # An argument that is no member is a value of the type (an Array's
    # length): an unbound parameter or anything but a count names no storage.
    if any(isinstance(a, TypeParamRef) or not isinstance(a, TpyType) and not (type(a) is int and a >= 0)
           for i, a in enumerate(args) if i not in (members.element, members.value)):
        return None
    return members


def declared_members(typ: object) -> tuple[TpyType, TpyType | None, bool] | None:
    """The members a native container or container view `typ` stores or
    views, as its stub declares them (`TypeDef.native_members`), at its type
    arguments (access wrappers kept): the element, the value a subscript
    yields when that is another member (or None), and whether a subscript is
    keyed by the element rather than by position. None for any other type,
    or one whose stub declares no element."""
    members = _native_members(typ)
    if members is None:
        return None
    args = typ.type_args
    return args[members.element], None if members.value is None else args[members.value], members.keyed


class MemberKind(Enum):
    """How a stored member is selected from the value holding it."""
    # A tuple's element, by position.
    ELEMENT = auto()
    # A sum's alternative, by its index among the union's members (an
    # Optional's payload is 1, beside the absent 0).
    ALTERNATIVE = auto()
    # A native container's declared member: 0 the element, 1 a dict's value.
    CONTAINED = auto()


@dataclass(frozen=True)
class MemberSelector:
    kind: MemberKind
    index: int


@dataclass(frozen=True)
class StoredMembers:
    """The members a stored value holds inline (`stored_members`), each
    with its selector, and how many its holder selects among (`arity`: a
    tuple's elements, a sum's alternatives -- an absent one counts though
    it is no member --, a container's members, a record's fields)."""
    members: tuple[tuple[object, TpyType], ...]
    arity: int


class Unexpanded(Enum):
    """The members of a value no rule enumerates: its reader takes it as
    holding anything."""
    UNEXPANDED = auto()


UNEXPANDED = Unexpanded.UNEXPANDED

# A record's fields as a reader knows them, each with its selector; None
# when the reader has none for that record. Readers differ in where fields
# come from and in what an unknown record means, so the source is theirs.
FieldSource = Callable[[NominalType], Iterable[tuple[object, TpyType]] | None]


def stored_members(typ: object, fields: FieldSource) -> 'StoredMembers | Unexpanded':
    """The members a value of `typ` holds inline when stored, one step deep,
    each with its selector: a tuple's elements, an Optional's payload, a
    union's alternatives in canonical member order (the void one has none),
    the members a native container declares (`declared_members`; a
    container view's, the members it views), a record's fields as `fields`
    gives them. A leaf holds none. Each member is as declared, its
    wrappers kept; the value's own `readonly` is seen through. An `Own[...]`
    is no value MIR models (storage never holds one: a field refuses the
    spelling), so neither the step nor a walk unwraps it: as a member it is
    one of a class no walk enters, as the value it is UNEXPANDED. In
    storage the form is fixed by the type mapping, so an Optional pinned to
    a pointer at a boundary (`force_pointer_repr`) still stores its payload
    inline. UNEXPANDED for a recursive alias union, a record `fields` does
    not know, and a type MIR does not model."""
    bare = unwrap_readonly(unwrap_ref_type(typ)) if isinstance(typ, TpyType) else typ
    match value_class(bare):
        case MIRClass.LEAF | MIRClass.OWNED_LEAF | MIRClass.VIEW:
            return StoredMembers((), 0)
        case MIRClass.TUPLE:
            return _selected(MemberKind.ELEMENT, bare.element_types)
        case MIRClass.OPTIONAL:
            return _selected(MemberKind.ALTERNATIVE, (None, bare.inner))
        case MIRClass.UNION if not bare.needs_wrapper():
            return _selected(MemberKind.ALTERNATIVE, tuple(None if is_void_like_type(t) else t for t in bare.members))
        case MIRClass.CONTAINER | MIRClass.CONTAINER_VIEW if (declared := declared_members(bare)) is not None:
            element, value, _ = declared
            return _selected(MemberKind.CONTAINED, (element, *(() if value is None else (value,))))
        case MIRClass.RECORD if (known := fields(bare)) is not None:
            known = tuple(known)
            return StoredMembers(known, len(known))
    return UNEXPANDED


def _selected(kind: MemberKind, members: tuple[TpyType | None, ...]) -> StoredMembers:
    """`members` each with its selector by position; an absent alternative
    (None) holds nothing, so it is no member, though it counts in the arity."""
    selected = tuple((MemberSelector(kind, i), t) for i, t in enumerate(members) if t is not None)
    return StoredMembers(selected, len(members))


def stored_types(typ: object, fields: FieldSource) -> 'tuple[TpyType, ...] | Unexpanded':
    """The types of `stored_members`, in order."""
    stored = stored_members(typ, fields)
    return stored if stored is UNEXPANDED else tuple(t for _, t in stored.members)


@dataclass(frozen=True)
class StoredStep:
    """One step of `walk_stored`: a type the walk reaches, its `readonly`
    seen through, with its class (`value_class`) and the selector that
    reached it (None at a root). `first` when the walk reaches the type for
    the first time -- only then may it enter it. `unexpanded` marks the
    step that follows an entered type's own when no rule enumerates its
    members (`stored_members` is UNEXPANDED): its reader takes it as
    holding anything."""
    type: object
    cls: 'MIRClass | None'
    selector: object | None
    first: bool
    unexpanded: bool = False


def walk_stored(roots: Iterable[object], fields: FieldSource,
                enter: Callable[[object, 'MIRClass | None'], bool], *,
                last_first: bool = False) -> Iterator[StoredStep]:
    """Every member step under storage of `roots`, at any depth, one
    `stored_members` step at a time: each root, then the members of each
    type the walk enters -- a type `enter` admits, on its first visit only,
    so a type reaching itself ends. A repeat is yielded too (`first`
    False): a reader answering on a selector sees every one. A type is
    entered only once the reader has taken its step, so `fields` is never
    asked for a record whose step the reader stopped at. The first member
    first, or the last when `last_first` (a stack's own order); the order
    decides which refusal a raising reader meets first."""
    def pushed(members: Iterable[tuple[object | None, object]]) -> list[tuple[object | None, object]]:
        members = list(members)
        return members if last_first else members[::-1]

    pending = pushed((None, r) for r in roots)
    seen: set[object] = set()
    while pending:
        selector, member = pending.pop()
        typ = unwrap_readonly(member)
        cls = value_class(typ)
        first = typ not in seen
        seen.add(typ)
        yield StoredStep(typ, cls, selector, first)
        if not first or not enter(typ, cls):
            continue
        stored = stored_members(typ, fields)
        if stored is UNEXPANDED:
            yield StoredStep(typ, cls, selector, False, True)
            continue
        pending.extend(pushed(stored.members))


def binds_cursor(typ: object) -> bool:
    """Whether iterating `typ` binds one element at a time (its declared
    element member): False for a type that yields both members at once."""
    members = _native_members(typ)
    return members is not None and members.cursor


UNSUPPORTED_ELEMENT = "unsupported native container element"
RECURSIVE_ELEMENT = "recursive container element"


def members_problem(typ: object, path: frozenset[TpyType] = frozenset()) -> str | None:
    """Why MIR does not model the members the native container or container
    view `typ` declares (`declared_members`), None when it does: every
    member is a container element (`element_problem`), and a type with a
    value member stores no record directly in either member. `path` holds
    the types the enclosing members' rule is walking through."""
    # The declared members: an `Own[...]` member is no element MIR models.
    declared = declared_members(typ) if value_class(typ) in (MIRClass.CONTAINER, MIRClass.CONTAINER_VIEW) else None
    if declared is None:
        return UNSUPPORTED_ELEMENT
    element, value, _ = declared
    members = (element,) if value is None else (element, value)
    for member in members:
        if len(members) > 1 and stores_record(member):
            return UNSUPPORTED_ELEMENT
        if (problem := element_problem(member, path)) is not None:
            return problem
    return None


def stores_record(typ: object) -> bool:
    """Whether storage of `typ` holds a record inline, directly: the record
    itself, or a wrapper with a member (`stored_members`, one level) that
    is no scalar or owned leaf -- a record, the only other member a
    modeled wrapper has (`wrapper_member_problem`)."""
    bare = unwrap_readonly(typ)
    if record_type(bare):
        return True
    if value_class(bare) not in WRAPPER_CLASSES:
        return False
    stored = stored_types(bare, lambda _: None)
    return stored is UNEXPANDED or not all(value_class(m) in (MIRClass.LEAF, MIRClass.OWNED_LEAF) for m in stored)


def modeled_members(typ: object) -> bool:
    """Whether MIR models the members the native container or container
    view `typ` declares (`members_problem`)."""
    return members_problem(typ) is None


# The dunders a native container operation dispatches on its elements
# (comparison, hashing): a record element declaring one runs user code
# inside the container's runtime operation.
_ELEMENT_DISPATCH_DUNDERS = ("__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__hash__")


def modeled_hierarchy(info: RecordInfo) -> tuple[RecordInfo, ...] | None:
    """`info` and its struct-base ancestors (`TypeRegistry.is_struct_base`),
    in MRO order, nearest first, when MIR models every one of them as a
    plain user record: not native, not a value type, not a typed dict, not
    generic (nor reached through a generic base), and no exception -- a
    return exception's `Exception` base builds no struct, so the ancestor
    walk alone would not refuse it. None otherwise, or when an ancestor does
    not resolve. Asked under the compilation that registered the records."""
    chain = [info]
    for ancestor in info.mro_ancestors:
        if not isinstance(ancestor, NominalType) or ancestor.type_args:
            return None
        td = type_def_of(ancestor)
        record = td.record if td is not None else None
        if record is None:
            return None
        if TypeRegistry.is_struct_base(info, record):
            chain.append(record)
    if any(r.is_native or r.is_value_type or r.is_typed_dict or r.type_params
           or r.is_return_exception or r.implements_throwable for r in chain):
        return None
    return tuple(chain)


def modeled_ancestors(typ: object) -> tuple[NominalType, ...] | None:
    """The struct-base ancestors of a modeled record `typ`
    (`modeled_hierarchy`), MRO order, nearest first, each spelled as an
    identity (`record_owner`); None for any other type."""
    chain = modeled_chain(typ)
    return None if chain is None else tuple(record_owner(r) for r in chain[1:])


def modeled_chain(typ: object) -> tuple[RecordInfo, ...] | None:
    """The modeled hierarchy (`modeled_hierarchy`) of record `typ`, from its
    TypeDef; None for any other type, and for a record with no TypeDef
    (lowered outside its compilation), which each reader reads by its own
    rule. Asked under the compilation that registered the records."""
    td = type_def_of(typ) if record_type(typ) else None
    return modeled_hierarchy(td.record) if td is not None and td.record is not None else None


def chain_fields(chain: tuple[RecordInfo, ...]) -> tuple[tuple[RecordInfo, object], ...]:
    """Each field storage of a record of modeled hierarchy `chain`
    (`modeled_chain`) contains, with the record declaring it, nearest record
    first: the one enumeration of a record's declared fields MIR reads."""
    return tuple((r, f) for r in chain for f in r.fields)


def typedef_fields(typ: NominalType) -> tuple[tuple[object, TpyType], ...] | None:
    """The field source (`stored_members`) of the records' declarations
    (`chain_fields`), each selected by its declaration at its declared
    type; None for a record whose hierarchy MIR does not model. A record
    with no TypeDef (lowered outside its compilation) has none, as
    `record_type` reads it."""
    if type_def_of(typ) is None:
        return ()
    chain = modeled_chain(typ)
    return None if chain is None else tuple((f, f.type) for _, f in chain_fields(chain))


def _plain_record_fields(typ: object) -> tuple[TpyType, ...] | None:
    """The field types -- inherited ones included -- of a record a native
    container may hold as an element: a record whose struct hierarchy MIR
    models (`modeled_hierarchy`) where no record declares a custom copy,
    move or destructor or a comparison or hash dunder (so no container
    operation can run user code on it). None for any other type. A record
    with no TypeDef (lowered outside its compilation) reads as plain with
    no fields, as `record_type` reads it, rather than failing closed."""
    if not record_type(typ):
        return None
    if type_def_of(typ) is None:
        return ()
    chain = modeled_chain(typ)
    if chain is None or any(r.has_copy or r.has_move or r.has_del for r in chain):
        return None
    if any(r.get_method_overloads(name) for r in chain for name in _ELEMENT_DISPATCH_DUNDERS):
        return None
    # The declared field types: an `Own[...]` field is no leaf MIR models.
    return tuple(t for _, t in typedef_fields(typ))


def plain_record_element(typ: object) -> bool:
    """A plain record (`_plain_record_fields`) whose fields are scalar
    leaves or owned leaves only, so a place inside it is at most one field
    deep. Asked under the compilation that registered the record."""
    fields = _plain_record_fields(typ)
    return fields is not None and all(storage_leaf(f) or owned_leaf(f) for f in fields)


def element_problem(typ: object, path: frozenset[TpyType] = frozenset()) -> str | None:
    """Why MIR does not model `typ` as a native container's member, None
    when it does -- the one member rule, at any depth: a scalar or owned
    leaf; a plain record (`_plain_record_fields`) whose every field is such
    a leaf or a native container whose members are container elements; or
    such a native container itself (`members_problem`); a wrapper, as an
    element or as such a record's field, by the wrapper rule
    (`wrapper_member_problem`). A type that reaches itself through its
    members is refused by name; `path` holds the types the walk is inside,
    so sibling members of one type stay admissible."""
    bare = unwrap_readonly(typ)
    if storage_leaf(bare) or owned_leaf(bare):
        return None
    if value_class(bare) in WRAPPER_CLASSES:
        return wrapper_member_problem(bare)
    if bare in path:
        return RECURSIVE_ELEMENT
    inside = path | {bare}
    if native_container_type(bare):
        return members_problem(bare, inside)
    fields = _plain_record_fields(bare)
    if fields is None:
        return UNSUPPORTED_ELEMENT
    for field in fields:
        if storage_leaf(field) or owned_leaf(field):
            continue
        if not element_record_member(field):
            return UNSUPPORTED_ELEMENT
        problem = (wrapper_member_problem(field) if value_class(field) in WRAPPER_CLASSES
                   else element_problem(field, inside))
        if problem is not None:
            return problem
    return None


def element_record_member(field: object) -> bool:
    """Whether a non-leaf field of an element record is one whose members
    the element rule walks (`element_problem`): a native container or a
    wrapper declared bare. A `readonly[...]` field of any class is
    refused."""
    return not isinstance(field, ReadonlyType) and value_class(field) in (MIRClass.CONTAINER, *WRAPPER_CLASSES)


def wrapper_member_problem(typ: object) -> str | None:
    """Why MIR does not model a record field or container element of
    wrapper type `typ` (a tuple, an Optional, a union) as a place, None
    when it does: each member it stores inline (`stored_members`, one
    level) is a scalar leaf, an owned leaf or a plain record
    (`plain_record_element`), its representation is storage (no
    `force_pointer_repr`), and nothing in it holds a loan (`loan_free`): a
    loan stored at a member's depth has no key. The one rule for both
    holders, the rule an Optional field's record payload has."""
    bare = unwrap_readonly(typ)
    if value_class(bare) not in WRAPPER_CLASSES or isinstance(bare, OptionalType) and bare.force_pointer_repr:
        return UNSUPPORTED_ELEMENT
    stored = stored_types(bare, lambda _: None)
    if stored is UNEXPANDED or not loan_free(bare):
        return UNSUPPORTED_ELEMENT
    # The declared members: an `Own[...]` or `readonly[...]` one is none of these.
    if not all(storage_leaf(m) or owned_leaf(m) or plain_record_element(m) for m in stored):
        return UNSUPPORTED_ELEMENT
    return None


def container_element(typ: object) -> bool:
    """A member type MIR models in a native container (`element_problem`)."""
    return element_problem(typ) is None


def leaf_global(typ: object) -> bool:
    """A type a leaf global binding (`THIRGlobalBinding`) carries: an inert
    scalar leaf or an owned leaf, which MIR reaches through a handle."""
    return storage_leaf(typ) or owned_leaf(typ)


def owned_value_type(typ: TpyType) -> TpyType | None:
    """The owned leaf a value of `typ` stores, seeing through the ownership
    wrapper and access modifiers; None for anything else."""
    typ = unwrap_readonly(typ)
    while isinstance(typ, OwnType):
        typ = unwrap_readonly(typ.wrapped)
    return typ if owned_leaf(typ) else None


def owned_constant(typ: object, value: object) -> bool:
    """Whether an owned leaf of `typ` holds the Python constant `value`: the
    value has the Python type of the leaf's declared zero value
    (`TypeDef.zero_value`)."""
    zero = zero_value_of(typ) if owned_leaf(typ) else None
    return zero is not None and type(value) is type(zero)


def primitive_leaf(typ: object) -> bool:
    """A scalar leaf whose TypeDef carries the primitive-operation contract
    (`TypeDef.primitive_ops`): its operators are the runtime's and it prints
    with no user method. An enum value is a leaf without it."""
    td = type_def_of(typ)
    return storage_leaf(typ) and td is not None and td.primitive_ops


def leaf_constant(typ: object, value: object) -> bool:
    """Whether a leaf of `typ` holds the Python constant `value` exactly: the
    value has the Python type of the leaf's declared zero value
    (`TypeDef.zero_value`) and lies inside a fixed-width int's range. A leaf
    without a declared zero (an enum) has no constants."""
    if not storage_leaf(typ):
        return False
    zero = zero_value_of(typ)
    if zero is None or type(value) is not type(zero):
        return False
    traits = int_traits_of(typ)
    if traits is not None:
        return traits.min_value <= value <= traits.max_value
    if isinstance(zero, str):
        return len(value) == 1 and value.isascii()
    return True


def record_type(typ: object) -> bool:
    """A record MIR models through a layout or a borrow: a non-generic,
    non-protocol nominal type whose TypeDef, when one is registered,
    declares a non-native record and no enum. Scalar leaves are never
    records: every primitive's TypeDef is native. User records and enums
    are TypeDefs of one compilation, so a caller lowers under
    `activate_compiler`; without one an enum reads as a record."""
    if not (isinstance(typ, NominalType) and typ.qualified_name() is not None
            and not typ.type_args and not typ.is_protocol):
        return False
    td = type_def_of(typ)
    return td is None or td.record is not None and not td.record.is_native and td.enum is None


def optional_record_field(typ: object) -> NominalType | None:
    """The payload record of a record member of declared type `typ` that is
    an Optional MIR models in place (`std::optional<P>` storage), or None:
    an Optional whose payload is a record, admitted by the wrapper rule
    (`wrapper_member_problem`: a plain record, so a payload place is at
    most one field deep and no user hook runs on its copy or destruction,
    holding no loan, so no stored-loan entry sits under the payload)."""
    bare = unwrap_readonly(typ)
    if not isinstance(bare, OptionalType) or wrapper_member_problem(bare) is not None:
        return None
    return bare.inner if record_type(bare.inner) else None


def converted_literal(typ: object, value: object) -> object | None:
    """The constant a number literal holding `value` is once converted into
    the leaf `typ`, or None when that leaf cannot hold it exactly. An int
    literal in a float context (`x * 1000`) is converted by C++ itself, a
    loan-free conversion that is exact up to the float's precision; every
    other literal must already be a constant of the leaf (`leaf_constant`).
    Kept apart from `leaf_constant`, which also decides which union
    alternative a literal names by its own Python type."""
    if leaf_constant(typ, value):
        return value
    floats = float_traits_of(typ) if storage_leaf(typ) else None
    if (floats is not None and type(value) is int and type(zero_value_of(typ)) is float
            and abs(value) <= 2 ** floats.significand_bits):
        return float(value)
    return None


class MIRClass(Enum):
    """The class of value MIR models a type as (`value_class`); how a
    position holds the value is the position's (`MIRHolding`)."""
    LEAF = auto()
    OWNED_LEAF = auto()
    VIEW = auto()
    RECORD = auto()
    CONTAINER = auto()
    CONTAINER_VIEW = auto()
    TUPLE = auto()
    OPTIONAL = auto()
    UNION = auto()


# The classes that store their members inline.
WRAPPER_CLASSES = frozenset({MIRClass.TUPLE, MIRClass.OPTIONAL, MIRClass.UNION})


def modeled_field(typ: object) -> bool:
    """Whether a record member of declared type `typ` has a shape MIR
    models: its class is one a field holds, a scalar leaf, an owned leaf
    or a view of one declared bare (a `readonly[...]` leaf member is no
    place MIR models), a native container or a record stored inline, or a
    tuple, Optional or union the wrapper rule admits
    (`wrapper_member_problem`), whose members MIR reaches through the
    field's own layout. The shape question only: whether a record's
    definition verifies is `MIRDefinitions`'s answer, never this
    predicate's."""
    match value_class(typ):
        case MIRClass.LEAF | MIRClass.OWNED_LEAF | MIRClass.VIEW:
            return not isinstance(typ, ReadonlyType)
        case MIRClass.TUPLE | MIRClass.OPTIONAL | MIRClass.UNION:
            return wrapper_member_problem(typ) is None
        case MIRClass.CONTAINER | MIRClass.RECORD:
            return True
    return False


def value_class(typ: object, representation: Representation = Representation.STORAGE) -> MIRClass | None:
    """The class of value MIR models `typ` as at `representation`, its
    access wrapper unwrapped; None for a type MIR does not model. An inert
    leaf is one only where the loan classifier proves it inert at that
    representation (`storage_leaf`)."""
    bare = unwrap_readonly(typ)
    if storage_leaf(bare, representation):
        return MIRClass.LEAF
    if owned_leaf(bare):
        return MIRClass.OWNED_LEAF
    if view_leaf(bare):
        return MIRClass.VIEW
    if container_view(bare):
        return MIRClass.CONTAINER_VIEW
    if native_container_type(bare):
        return MIRClass.CONTAINER
    if isinstance(bare, TupleType):
        return MIRClass.TUPLE
    if isinstance(bare, OptionalType):
        return MIRClass.OPTIONAL
    if isinstance(bare, UnionType):
        return MIRClass.UNION
    if record_type(bare):
        return MIRClass.RECORD
    return None
