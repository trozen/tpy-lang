# v1.5 M2 coverage gaps: pin the root-class dynamic_cast path (isinstance
# against BaseException itself) and the deep-chain path (FileNotFoundError,
# which is 3 levels under BaseException: FileNotFoundError <- OSError <-
# Exception <- BaseException). The deep-chain check exercises the Phase 18
# transitive-override propagation through three records.

from typing import Optional


class Suppress:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is None:
            return False
        # Deep-chain dispatch: FileNotFoundError sits three levels under
        # BaseException. Hits before the broader OSError arm.
        if isinstance(exc_val, FileNotFoundError):  # tpyc: ok
            print("FNFE: " + str(exc_val))
            return True
        if isinstance(exc_val, OSError):  # tpyc: ok
            print("OS: " + str(exc_val))
            return True
        # Root-class isinstance: same as `is not None` for Optional[BaseException],
        # but pins the codegen of dynamic_cast against the root type.
        if isinstance(exc_val, BaseException):  # tpyc: ok
            print("BASE: " + str(exc_val))
            return False  # propagate
        return False


def main() -> None:
    with Suppress():
        raise FileNotFoundError("missing.txt")
    print("after FNFE")

    with Suppress():
        raise OSError("io")
    print("after OS")

    try:
        with Suppress():
            raise RuntimeError("re")
    except RuntimeError as e:
        print("propagated RE: " + str(e))


main()
