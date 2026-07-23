# A narrowed str|None FIELD (optional<String> storage, the
# reference-flavor sibling of the Int32 case) derefs through the widened
# yield sink when no suspension intervenes. (The frame-reassign face
# `q = self.s` hits the pre-existing view-form frame-local gap --
# BUGS.md str-local-across-suspension entry -- so it stays out.)
from typing import Iterator


class Box:
    s: str | None

    def __init__(self, v: str | None) -> None:
        self.s = v

    def gen_field(self) -> Iterator[str]:
        if self.s is not None:
            yield self.s
        yield "end"


def main() -> None:
    b = Box("v")
    for x in b.gen_field():
        print(x)
    n = Box(None)
    for x in n.gen_field():
        print(x)


main()
