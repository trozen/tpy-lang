# datetime v3 documented divergence: TPy cannot express timezone.utc (a
# class-level constant of the record's own type); the supported spelling
# is the module-level UTC alias (CPython 3.11+). The attribute access must
# stay a loud compile error, never a silent wrong value.
from datetime import timezone


def main() -> None:
    print(timezone.utc)  # tpyc: error(/not a variable/)


main()
