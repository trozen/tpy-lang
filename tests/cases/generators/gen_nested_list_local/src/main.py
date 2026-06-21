# A generator frame holding a list-of-lists local across a yield: the frame
# field (and the for-loop element type) carry a pending list element that must
# be finalized, or the resumable-frame hoist / for-loop strategy crashes.
from typing import Iterator
def gen() -> Iterator[int]:
    rows = [[i, i + 1] for i in range(3) if i > 0]
    for r in rows:
        yield r[0]
        yield r[1]
def main() -> None:
    for v in gen():
        print(v)
main()
