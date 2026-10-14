# Sibling native module sharing the parent's cpp_namespace
__tpy__ = {"mod": "native",
           "cpp_namespace": "mypkg"}
from tpy.extern import native

@native("mypkg::greet")
def greet(name: str) -> str: ...
