from b import B
from tpy import Int32
from typing import overload

def helper() -> Int32:
    return 42

class A:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v

    # Non-generic record, non-templated method, but @overload-grouped:
    # codegen keeps the impl inline-in-struct (each overload variant is
    # emitted under a different mangled name in the struct), so the
    # .hpp body needs B's complete layout. The gate must fire via the
    # `id(method) in overload_groups` branch.
    @overload
    def merge(self, x: B) -> Int32: ...
    @overload
    def merge(self, x: Int32) -> Int32: ...
    def merge(self, x: B | Int32) -> Int32:  # tpyc: error(/Cyclic import/)
        if isinstance(x, Int32):
            return self.val + x
        return self.val + x.payload
