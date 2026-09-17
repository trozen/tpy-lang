# Enum .name feeding owned-str sinks: sema types .name as StrView (the value
# is a static-storage string_view), so owned sinks copy via the standard
# view->owned machinery (previously ill-formed C++ -- a bare view render
# into std::string). View-consuming positions stay copy-free.
from enum import Enum
from typing import Iterator

from tpy import int32


class Color(Enum):
    RED = 1
    GREEN = 2


def owned_return(c: Color) -> str:
    return c.name  # tpyc: ok


def owned_local(c: Color) -> str:
    label: str = c.name  # tpyc: ok
    label += "!"
    return label


def view_positions(c: Color) -> None:
    v = c.name  # tpyc: type(StrView)
    print(v, len(v))
    print(c.name == "RED")
    print(f"<{c.name}>")
    # Annotated but never mutated: resolves as a view of the static member
    # name -- the annotation's coerce is stale and renders bare (no copy).
    label: str = c.name  # tpyc: ok
    print(label)


def owned_slot(c: Color) -> None:
    xs: list[str] = []
    # The element slot's view->owned convert renders inline -- the source is
    # a view read, so there is nothing to move out of and no temp to hoist.
    xs.append(c.name)  # tpyc: ok
    print(xs)


# Frame position: the member-name table is STATIC, so the slot stays a view
# across the suspension where any other field-shaped source would resolve
# owned (the frame outlives the case-block temps a sync body may borrow).
def frame_view(c: Color) -> Iterator[int32]:
    label: str = c.name  # tpyc: type(StrView)
    yield len(label)
    print("frame", label)


def main() -> None:
    print(owned_return(Color.RED))
    print(owned_local(Color.GREEN))
    view_positions(Color.RED)
    owned_slot(Color.GREEN)
    for n in frame_view(Color.RED):
        print("frame yield", n)


main()
