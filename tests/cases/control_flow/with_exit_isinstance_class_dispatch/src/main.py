# v1.5 M2: class-based exception dispatch in __exit__ bodies via
# isinstance(exc_val, X). BaseException is @dynamic-rooted (inherits
# Throwable), so the codegen lowers isinstance to dynamic_cast on the
# const BaseException* pointer that flows into __exit__.

from typing import Optional


class SuppressVE:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is None:
            return False
        if isinstance(exc_val, ValueError):  # tpyc: ok
            print("suppressed VE: " + str(exc_val))
            return True
        if isinstance(exc_val, (RuntimeError, OSError)):  # tpyc: ok -- tuple form
            print("suppressed family: " + str(exc_val))
            return True
        return False


def main() -> None:
    with SuppressVE():
        raise ValueError("bad value")
    print("after VE")

    try:
        with SuppressVE():
            raise RuntimeError("re")
    except RuntimeError:
        print("unexpected re-raise")
    else:
        print("after RE")

    try:
        with SuppressVE():
            raise IndexError("ix")
    except IndexError as e:
        print("propagated: " + str(e))


main()
