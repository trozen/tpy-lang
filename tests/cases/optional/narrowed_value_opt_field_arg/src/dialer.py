# Companion module: the callee whose second parameter is a value-repr Optional.
from tpy import Int32


def dial(host: str, timeout: float | None) -> Int32:
    return 0 if timeout is None else 1
