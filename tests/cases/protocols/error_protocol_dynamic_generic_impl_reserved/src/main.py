# `__tpy_Impl` is reserved by the @dynamic adapter codegen as the
# concrete-impl template parameter on the Adapter/RefAdapter partial
# specs. A user-declared protocol type param with the same name would
# silently collide at C++ template instantiation. The `__tpy_` prefix
# is the project's reserved-identifier namespace; users should not
# pick names that start with it.
from typing import Protocol
from tpy import dynamic


@dynamic
class Container[__tpy_Impl](Protocol):  # tpyc: error(/cannot declare a type parameter named '__tpy_Impl'/)
    def get(self) -> __tpy_Impl:
        ...
