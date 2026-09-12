# A standalone unpack target whose element is a value-repr `int32 | None`: the
# fresh target registers a value-opt binding, so its narrowed read derefs.
from tpy import int32


def total(p: tuple[int32 | None, int32]) -> int32:
    a, b = p
    if a is not None:
        return a + b  # the narrowed value-opt deref
    return b


def main() -> None:
    print(total((3, 4)))
    print(total((None, 9)))


main()
