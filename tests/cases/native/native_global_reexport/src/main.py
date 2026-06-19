# Cross-module re-export of a native_global through a non-entry module's
# header. Exercises both the name path (`from mid import GLOBAL_VAL`) and the
# function path (`use_global()` reads the re-exported native_global).
from mid import use_global, use_normal, GLOBAL_VAL, NORMAL_VAL


def main() -> None:
    print(use_global())   # native_global value (42)
    print(GLOBAL_VAL)     # re-exported through mid, used directly here
    print(use_normal())   # plain Final re-export still works (inverse case)
    print(NORMAL_VAL)


main()
