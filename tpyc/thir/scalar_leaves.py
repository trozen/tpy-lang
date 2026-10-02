"""The scalar leaves and records THIR storage facts and MIR model."""

from ..type_def_registry import (
    NativeMembers, float_traits_of, int_traits_of, is_borrowing_view_type, type_def_of, zero_value_of,
)
from ..typesys import (
    NominalType, OwnType, ReadonlyType, Representation, TpyType, TypeParamRef, is_inert_leaf, is_owned_leaf,
    is_primitive_owned_leaf, unwrap_readonly, unwrap_ref_type, view_family_of, view_owned_leaf,
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


def binds_element(loop_type: TpyType, element: TpyType) -> bool:
    """Whether a loop variable of `loop_type` binds a container element of
    type `element`: the element itself (a scalar copy, a record alias), or
    a view of an owned-leaf element (a `StrView` key of a `dict[str, V]`)."""
    loop = unwrap_readonly(unwrap_ref_type(loop_type))
    return loop == element or owned_leaf(element) and view_compatible(loop, element)


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


def readonly_elements(typ: NominalType) -> bool:
    """A view whose element argument is readonly (`Span[readonly[T]]`):
    no write goes through it."""
    return any(isinstance(a, ReadonlyType) for a in typ.type_args)


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


def binds_cursor(typ: object) -> bool:
    """Whether iterating `typ` binds one element at a time (its declared
    element member): False for a type that yields both members at once."""
    members = _native_members(typ)
    return members is not None and members.cursor


def modeled_members(typ: object) -> bool:
    """Whether MIR models the members the native container or container
    view `typ` declares (`declared_members`): every member is a container
    element (`container_element`), and a type with a value member holds
    scalar or owned leaves only, in both members."""
    members = declared_members(typ)
    if members is None:
        return False
    element, value, _ = members
    leaves_only = value is not None
    return all(storage_leaf(m) or owned_leaf(m) or not leaves_only and plain_record_element(m)
               for m in (unwrap_readonly(element), *(() if value is None else (unwrap_readonly(value),))))


# The dunders a native container operation dispatches on its elements
# (comparison, hashing): a record element declaring one runs user code
# inside the container's runtime operation.
_ELEMENT_DISPATCH_DUNDERS = ("__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__hash__")


def plain_record_element(typ: object) -> bool:
    """A record a native container may hold as an element MIR models: a
    non-generic, non-native reference record with no parents, no custom
    copy, move or destructor, no comparison or hash dunder (so no container
    operation can run user code on it), and whose fields are scalar leaves
    or owned leaves (so an element place is at most one field deep). Asked
    under the compilation that registered the record."""
    if not record_type(typ):
        return False
    td = type_def_of(typ)
    if td is None:
        # As `record_type`: a record with no TypeDef (lowered outside its
        # compilation) reads as plain rather than failing closed.
        return True
    info = td.record
    if (info is None or info.is_native or info.is_value_type or info.is_typed_dict
            or info.parents or info.type_params
            or info.has_copy or info.has_move or info.has_del):
        return False
    if any(info.get_method_overloads(name) for name in _ELEMENT_DISPATCH_DUNDERS):
        return False
    return all(storage_leaf(f.type) or owned_leaf(f.type) for f in info.fields)


def container_element(typ: object) -> bool:
    """An element type MIR models in a native container: a scalar leaf, an
    owned leaf, or a plain record (`plain_record_element`)."""
    return storage_leaf(typ) or owned_leaf(typ) or plain_record_element(typ)


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
