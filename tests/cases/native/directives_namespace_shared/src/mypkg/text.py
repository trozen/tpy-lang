# Sibling native module sharing the parent's cpp_namespace
# tpy: native_module
# tpy: cpp_namespace("mypkg")
from tpy.extern import native

@native("mypkg::greet")
def greet(name: str) -> str: ...
