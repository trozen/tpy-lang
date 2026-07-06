# bytearray is a mutable reference type sharing bytes' C++ representation
# (std::vector<uint8_t>); marshalling it by copy would silently drop the aliasing
# CPython callers expect, so only the immutable value type `bytes` is admitted.
# tpy: ext_module
from tpy.extern import export


@export
def f(x: bytearray) -> int:  # tpyc: error(/parameter 'x'.*cannot cross the CPython boundary/)
    return 0
