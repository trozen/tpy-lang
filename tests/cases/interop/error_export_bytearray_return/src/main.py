# Own[bytearray] must be rejected, not silently marshalled as bytes: Own only
# transfers ownership of its inner type, so admission judges the inner bytearray
# (a mutable reference type), not the Own wrapper (whose is_value_type is always
# True). A bare `-> bytearray` is blocked earlier by the return-by-reference
# check; Own[bytearray] is the form that reaches the boundary validator.
# tpy: ext_module
from tpy import Own
from tpy.extern import export


@export
def f() -> Own[bytearray]:  # tpyc: error(/return type.*not yet marshallable/)
    return bytearray(b"x")
