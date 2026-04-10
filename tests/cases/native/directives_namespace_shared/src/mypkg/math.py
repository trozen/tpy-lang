# Sibling native module sharing the parent's cpp_namespace
# tpy: cpp_namespace("mypkg")
from tpy import Int32
from tpy.extern import native

@native("mypkg::add")
def add(a: Int32, b: Int32) -> Int32: ...
