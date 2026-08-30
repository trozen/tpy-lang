# A bytes literal against a bytes-family SUBJECT, reachable through Optional
# (a bare bytes subject is turned away earlier, by the subject-type gate). The
# kind matches, so this names an unimplemented comparison, not a type error --
# the counterpart to the genuine mismatch in error_literal_bytes_switch_label.
from typing import Optional


def classify(b: Optional[bytes]) -> str:
    match b:
        case None:
            return "none"
        case b"z":  # tpyc: error(/bytes literal pattern against subject type 'bytes' is not yet implemented/)
            return "z"
        case _:
            return "other"


def main() -> None:
    print(classify(None))


main()
