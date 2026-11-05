# An owning iterator from iter() panics when moved: its cursor points
# into the moved-from source, and restarting would replay the pull.
__tpy__ = {"includes": ["native_types.hpp"]}
from typing import Iterator
from tpy import char
from tpy.extern import native


@native("probe::moved_prefix")
def moved_prefix() -> Iterator[char]: ...


def main() -> None:
    print("before")
    # the C++ side moves the iterator after a pull: a panic in every build
    print(list(moved_prefix()))


main()
