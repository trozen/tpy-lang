# sys.platform is Final (the build target's value): a write is rejected.
# Valid Python rejected as a documented restriction: another module's Final
# cannot be reassigned (docs/LANGUAGE_FEATURES.md "Final Constants"
# semantics); CPython lets a program rebind it.
import sys


def main() -> None:
    sys.platform = "x"  # tpyc: error(/Cannot reassign Final variable 'sys.platform'/)


main()
