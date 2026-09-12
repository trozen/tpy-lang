# Direct inheritance of a generic @dynamic protocol with T in method
# parameter position is rejected at sema. Without this guard, codegen
# would emit an override with the concrete type while the base virtual
# uses `param_val_or_ref_t<T>` -- mismatched signatures, derived class
# stays abstract, C++ build fails with a wall of template errors and no
# tpyc diagnostic. The structural-conformance (adapter) path handles
# this shape correctly; users should use that.
from typing import Protocol
from tpy import int32, dynamic


@dynamic
class Sink[T](Protocol):
    def put(self, val: T) -> None:
        ...


class IntSink(Sink[int32]):  # tpyc: error(/cannot directly inherit generic.*'put'.*type parameter 'T' in parameter position/)
    def put(self, val: int32) -> None:
        pass
