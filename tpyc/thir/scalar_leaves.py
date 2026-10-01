"""The scalar leaves and records THIR storage facts and MIR model."""

from ..type_def_registry import float_traits_of, int_traits_of, type_def_of, zero_value_of
from ..typesys import (
    NominalType, OwnType, Representation, TpyType, is_inert_leaf, is_owned_leaf, is_primitive_owned_leaf,
    unwrap_readonly,
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
