# Sibling native module sharing the parent's cpp_namespace
# tpy: native_module
# tpy: cpp_namespace("mypkg")
from tpy import int32
from tpy.extern import native

@native("mypkg::add")
def add(a: int32, b: int32) -> int32: ...
