__tpy__ = {"mod": "native", "cpp_namespace": "nm"}
from tpy import int32
from tpy.extern import native

@native
def f(x: int32) -> int32: ...

# A native module generates no code, so a statement there would never run.
print("side effect")  # tpyc: error(/module-level statement not allowed in a native module .*'nm' is declaration-only/)
