# Helper module: provides tag() function used by the macro.
from tpy import int32


def tag(label: str, value: int32) -> str:
    return label + "=" + str(value)
