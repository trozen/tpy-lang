# isinstance narrowing must work on module-level Any globals -- not just
# function-locals. Globals aren't in the codegen's var_types map, so the
# Any-isinstance path needs to fall through to the registry's ModuleInfo
# to recognise the declared type.

from typing import Any


g: Any = 42


def main() -> None:
    if isinstance(g, int):
        print(g + 1)
    if isinstance(g, str):
        print("never")


main()
