# Mixed module: @native (C++) and @native_c (C) functions
# tpy: include("native_types.hpp")
from tpy import Int32
from tpy.extern import native, native_c

# @native with namespace -- should produce ::myns::namespaced_add()
@native("myns::namespaced_add")
def ns_add(a: Int32, b: Int32) -> Int32: ...

# @native with bare name -- should produce ::bare_add()
@native("bare_add")
def bare(a: Int32, b: Int32) -> Int32: ...

# @native_c -- should produce ::tpyapp::lib::c_multiply() (module-qualified)
@native_c("c_multiply")
def c_mul(a: Int32, b: Int32) -> Int32: ...
