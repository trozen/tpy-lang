# @nocopy narrowed Optional local used after consume point -> error.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    value: int32


def consume(h: Own[Handle]) -> int32:
    return h.value


def test() -> int32:
    h: Handle | None = Handle()
    h.value = int32(1)
    assert h is not None
    result = consume(h)  # tpyc: error(/used after this point/)
    print(h.value)
    return result
