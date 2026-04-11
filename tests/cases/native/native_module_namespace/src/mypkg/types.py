# Test cpp_namespace auto-prefix: bare @native uses namespace prefix
# tpy: native_module
# tpy: cpp_namespace("mypkg")
# tpy: include("native_types.hpp")
from tpy import Int32, Ptr, Own
from tpy.extern import native

# Bare @native -- auto-prefixed to mypkg::Vec2
@native
class Vec2:
    x: Int32
    y: Int32

# Bare @native function -- auto-prefixed to mypkg::add_vecs
@native
def add_vecs(a: Vec2, b: Vec2) -> Own[Vec2]: ...

# Explicit name overrides auto-prefix
@native("other::Thing")
class Thing:
    pass
