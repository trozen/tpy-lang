# A parameter default naming a nonexistent enum member is rejected at
# registration (the param-side of the shared enum-default check, mirroring the
# field-side error_enum_field_default_bad_member). TPy-only -- CPython would
# raise AttributeError at def-evaluation, not a clean compile error.
from enum import Enum


class Color(Enum):
    RED = 0
    GREEN = 1


def paint(c: Color = Color.PURPLE) -> Color:  # tpyc: error(/'PURPLE' is not a member of enum 'Color'/)
    return c


def main() -> None:
    print(int(paint().value))


main()
