# Regression: @native ValueType record's is_value_type spec must target
# the rename namespace (record.native_name), not the module's namespace.
# Previously emitted `tpy::is_value_type<<module-ns>::Handle>` for a non-
# existent type and failed with "template argument 1 is invalid".
#
# Also covers the follow-up gap: native ValueType records were filtered out
# of the per-record emit loop, so `is_value_type<MyHandle>` defaulted to
# false_type. A [T: ValueType] bounded generic call expands its `requires`
# clause via `is_value_type<T>::value`, which would have failed to satisfy
# on MyHandle without an explicit specialization.
# tpy: cpp_namespace("tpyapp::myns")
# tpy: include("native_types.hpp")
from tpy import int32, ValueType
from tpy.extern import native


@native("::MyHandle")
class Handle(ValueType):
    val: int32


def identity[T: ValueType](x: T) -> T:
    return x


def main() -> None:
    h = Handle(42)
    print(h.val)

    copied = identity(h)
    print(copied.val)


main()
