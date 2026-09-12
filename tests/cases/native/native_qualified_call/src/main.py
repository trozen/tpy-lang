# Test module-qualified calls to @native vs @native(binding="C") functions
# @native: use native name directly (::ns::func or ::bare_func)
# @native(binding="C"): use module-qualified name (::tpyapp::lib::func)
import lib
from tpy import int32

def main() -> None:
    # @native("myns::namespaced_add") -> ::myns::namespaced_add(10, 32)
    print(lib.ns_add(int32(10), int32(32)))

    # @native("bare_add") -> ::bare_add(10, 32)
    print(lib.bare(int32(10), int32(32)))

    # @native("c_multiply", binding="C") -> ::tpyapp::lib::c_multiply(6, 7)
    print(lib.c_mul(int32(6), int32(7)))

main()
