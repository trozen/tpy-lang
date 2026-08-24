# `str` in a C-linkage signature is rejected by the C-ABI gate: there is no way
# to spell it in C, and respelling the param `const char*` while the body still
# reads it as a view makes `s == "hi"` a pointer compare rather than a content
# compare.
from tpy.extern import native, export
from tpy import Int32

# The inbound direction: a C function declared to take a TPy str.
@native(binding="C")
def puts(s: str) -> Int32: ...  # tpyc: error(/parameter 's': type 'str' is not representable in the C ABI; use Ptr\[readonly\[UInt8\]\] and convert with tpy.unsafe.unsafe_str_from_cstr\(\) . unsafe_cstr\(\)/)

# The outbound direction is rejected the same way; sema raises on the first
# violation, so only the declaration above is reported.
@export(binding="C")
def greet(name: str) -> None:
    puts(name)
