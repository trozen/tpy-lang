# The char-at-a-str-slot admission is the str family only, as the "a `char` ...
# is admitted at these `str` / `String` / `StrView` slots" rule in
# docs/LANGUAGE_FEATURES.md (Strings) enumerates: a bytes element slot has no
# conversion from char and must keep rejecting.
from typing import Final

CHARS: Final[str] = "abc"


def main() -> None:
    raw: list[bytes] = [CHARS[0]]  # tpyc: error(/has type char, incompatible with annotated element type bytes/)
    print(len(raw))


main()
