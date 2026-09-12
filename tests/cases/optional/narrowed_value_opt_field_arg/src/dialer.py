# Companion module: the callee whose second parameter is a value-repr Optional.
from tpy import int32


def dial(host: str, timeout: float | None) -> int32:
    return 0 if timeout is None else 1
