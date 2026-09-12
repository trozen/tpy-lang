from b import B
from tpy import int32
from typing import overload

def helper() -> int32:
    return 42

class A:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v

    # Non-generic record, non-templated method, but @overload-grouped:
    # codegen keeps the impl inline-in-struct (each overload variant is
    # emitted under a different mangled name in the struct), so the
    # .hpp body needs B's complete layout. The gate must fire via the
    # `id(method) in overload_groups` branch.
    @overload
    def merge(self, x: B) -> int32: ...
    @overload
    def merge(self, x: int32) -> int32: ...
    def merge(self, x: B | int32) -> int32:  # tpyc: error(/Cyclic import/)
        if isinstance(x, int32):
            return self.val + x
        return self.val + x.payload
