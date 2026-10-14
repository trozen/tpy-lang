# A non-value native_global re-exported by `mid` and read by NAME is
# refused (BUGS.md#native-global-own-nonvalue-ptr-slot); the module
# attribute `mid.xs` compiles (native/native_global_reexport).
from mid import xs


def main() -> None:
    xs.append(4)  # tpyc: error(/method\.recv\.name_absent/)
    print(len(xs))


main()
