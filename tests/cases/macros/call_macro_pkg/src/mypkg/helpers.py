# Helper module: provides tag() function used by the macro.
from tpy import Int32


def tag(label: str, value: Int32) -> str:
    return label + "=" + str(value)
