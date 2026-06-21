# A view-family (str/bytes) for-loop variable in a resumable generator used to
# crash: its pending view type wasn't resolved before the frame-local hoist.
from typing import Iterator
from tpy import Int32


def words_gen(words: list[str]) -> Iterator[Int32]:
    i = 0
    while i < 2:
        yield i
        yield i + 1            # second yield forces the resumable-frame path
        for w in words:
            print(w)
        i += 1


def blobs_gen(blobs: list[bytes]) -> Iterator[Int32]:
    i = 0
    while i < 2:
        yield i
        yield i + 1
        for b in blobs:
            print(len(b))
        i += 1


# tuple-unpack loop var: the str element `name` is the pending-view target.
def pairs_gen(pairs: list[tuple[str, Int32]]) -> Iterator[Int32]:
    i = 0
    while i < 2:
        yield i
        yield i + 1
        for name, n in pairs:
            print(name, n)
        i += 1


def main() -> None:
    ws: list[str] = []
    ws.append("a")
    ws.append("bb")
    seen = 0
    for v in words_gen(ws):
        seen += 1
        if seen >= 4:
            break

    bs: list[bytes] = []
    bs.append(b"xyz")
    seen = 0
    for v in blobs_gen(bs):
        seen += 1
        if seen >= 4:
            break

    ps: list[tuple[str, Int32]] = []
    ps.append(("k", 9))
    seen = 0
    for v in pairs_gen(ps):
        seen += 1
        if seen >= 4:
            break


main()
