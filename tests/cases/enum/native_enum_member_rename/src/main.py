# @native enum where one C++ member name (None) is a Python keyword.
# Uses native_member("None") to alias the TPy-side name to the C++ symbol.
# tpy: include("native_types.hpp")
from enum import Enum, auto
from tpy.extern import native, native_member


@native("cfg::Mode")
class Mode(Enum):
    NONE_MODE = native_member("None")
    AUTO = native_member("Auto")
    MANUAL = native_member("Manual")


def describe(m: Mode) -> str:
    match m:
        case Mode.NONE_MODE:
            return "off"
        case Mode.AUTO:
            return "auto"
        case Mode.MANUAL:
            return "manual"


def main() -> None:
    print(Mode.NONE_MODE)
    print(Mode.AUTO)
    print(Mode.MANUAL)
    print(describe(Mode.NONE_MODE))
    print(describe(Mode.AUTO))
    print(Mode.MANUAL.name)
    print(Mode.MANUAL.value)


main()
