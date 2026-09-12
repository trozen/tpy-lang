# Mixed module: @native (C++) and @native(binding="C") (C) functions
# tpy: include("native_types.hpp")
from tpy import int32
from tpy.extern import native

# @native with namespace -- should produce ::myns::namespaced_add()
@native("myns::namespaced_add")
def ns_add(a: int32, b: int32) -> int32: ...

# @native with bare name -- should produce ::bare_add()
@native("bare_add")
def bare(a: int32, b: int32) -> int32: ...

# @native(binding="C") -- should produce ::tpyapp::lib::c_multiply() (module-qualified)
@native("c_multiply", binding="C")
def c_mul(a: int32, b: int32) -> int32: ...
