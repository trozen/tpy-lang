"""The scalar leaves and records THIR storage facts and MIR model."""

from ..type_def_registry import int_traits_of, type_def_of, zero_value_of
from ..typesys import NominalType, Representation, is_inert_leaf


def storage_leaf(typ: object, representation: Representation = Representation.STORAGE) -> bool:
    """Whether `typ` is a scalar leaf at `representation`: a non-generic
    nominal type the loan classifier (`typesys.loan_class`) proves inert
    there -- the primitives by `TypeDef.loan_inert`, and enum values. A
    wrapper (readonly, Own, Literal) or aggregate is never a leaf itself."""
    return isinstance(typ, NominalType) and not typ.type_args and is_inert_leaf(typ, representation)


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
