# Cross-module re-export of a native_global through a non-entry module's
# header. Exercises the name path (`from mid import GLOBAL_VAL`), the
# module-attribute path (`mid.GLOBAL_VAL`) and the function path
# (`use_global()` reads the re-exported native_global).
import mid
from mid import use_global, use_normal, GLOBAL_VAL, NORMAL_VAL


def main() -> None:
    print(use_global())   # native_global value (42)
    print(GLOBAL_VAL)     # re-exported through mid, used directly here
    print(use_normal())   # plain Final re-export still works (inverse case)
    print(NORMAL_VAL)
    # module attribute: renders the native symbol, not a `mid` qname
    print(mid.GLOBAL_VAL)  # tpyc: ok
    print(mid.NORMAL_VAL)
    # a re-exported NON-value native_global through the module attribute:
    # the entry carries leaf's native symbol, so no `(*slot)` deref
    mid.xs.append(2)  # tpyc: ok
    print(len(mid.xs))  # tpyc: ok


main()
