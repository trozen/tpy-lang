# Helper module for the module-qualified call section.
from tpy import Own, int32


def pair() -> Own[tuple[list[str], int32]]:
    return (["q"], 3)
