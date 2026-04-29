# Tuple literal with Any-typed slots gets the same int/float-literal
# storage upgrade as list/dict/set: ints stored as BigInt so cast(int,
# t[0]) round-trips. Otherwise `tuple[Any, Any] = (1, 2.5)` would
# silently store Int32 / FloatLiteral and panic on later cast.

from typing import Any


def main() -> None:
    t: tuple[Any, Any] = (1, 2.5)
    print("ok")


main()
