# Test module-qualified calls to @native vs @native_c functions
# @native: use native name directly (::ns::func or ::bare_func)
# @native_c: use module-qualified name (::tpyapp::lib::func)
import lib
from tpy import Int32

def main() -> None:
    # @native("myns::namespaced_add") -> ::myns::namespaced_add(10, 32)
    print(lib.ns_add(Int32(10), Int32(32)))

    # @native("bare_add") -> ::bare_add(10, 32)
    print(lib.bare(Int32(10), Int32(32)))

    # @native_c("c_multiply") -> ::tpyapp::lib::c_multiply(6, 7)
    print(lib.c_mul(Int32(6), Int32(7)))

main()
