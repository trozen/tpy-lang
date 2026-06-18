# A view (StrView) derived from a parameter has safe provenance: yielding it
# from a generator (or returning it from a lambda) is sound -- the param data
# outlives the call -- and must not be rejected as a dangling local view.
from tpy import StrView
from typing import Iterator

def tails(s: str) -> Iterator[StrView]:
    yield s[1:]
    yield s[2:]

def main() -> None:
    for t in tails("hello"):
        print(t)

main()
