# Non-native leaf reached through a dotted chain whose parent packages
# are `# tpy: native_module`. Before the fix, the codegen parent-walk
# emitted __tpy_init() calls for the native parents; their namespaces
# are undeclared (no .cpp output), failing the C++ build. The fix
# skips parent-init emission for modules without a runtime init.
from pkg.inner.leaf import COUNTER
from tpy import Int32


def main() -> None:
    print(COUNTER)
    print(COUNTER + Int32(1))


main()
