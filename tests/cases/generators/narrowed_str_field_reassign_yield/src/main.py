# A str local reassigned from a narrowed str|None FIELD and yielded
# post-suspension: the frame view-local promotion resolves q owned, so
# the owned yield slot converts cleanly (was a string_view frame field
# failing the C++ build).
from typing import Iterator


class Box:
    s: str | None

    def __init__(self, v: str | None) -> None:
        self.s = v

    def gen_reassigned(self) -> Iterator[str]:
        q = ""
        if self.s is not None:
            # The reassign from the narrowed field forces q's frame
            # storage owned.
            q = self.s
        yield "start"
        yield q


def main() -> None:
    for x in Box("v").gen_reassigned():
        print(x)
    for x in Box(None).gen_reassigned():
        print(x)


main()
