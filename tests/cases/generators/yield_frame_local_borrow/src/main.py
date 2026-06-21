# Yield-by-reference of a frame-resident local: the consumer's in-place mutation
# of the yielded borrow is visible to the generator on resume (the os.walk
# topdown-prune contract). Each case mutates across the yield and observes it.
from typing import Iterator


def counter() -> Iterator[list[int]]:
    buf: list[int] = []
    buf.append(1)
    yield buf                 # tpyc: ok
    # consumer appended below; the generator sees it
    print("resume sees:", buf)
    buf.append(99)
    yield buf                 # tpyc: ok


def mutate_observe() -> None:
    seen = 0
    for v in counter():
        seen += 1
        if seen == 1:
            v.append(2)       # mutation aliases the frame slot


# os.walk shape: yield a tuple of frame-local lists; consumer prunes in place.
def walk() -> Iterator[tuple[str, list[str], list[str]]]:
    dirs: list[str] = []
    dirs.append("a")
    dirs.append("skip")
    dirs.append("b")
    files: list[str] = []
    files.append("f1")
    yield ("root", dirs, files)   # tpyc: ok
    print("after prune, dirs =", dirs)


def walk_prune() -> None:
    for name, dirs, files in walk():
        kept: list[str] = []
        for d in dirs:
            if d != "skip":
                kept.append(d)
        dirs[:] = kept            # in-place prune, os.walk-style


# A frame-local yielded with no post-yield read is still hoisted (frame-resident),
# so the borrow is sound. This case asserts the compile-time hoisting/rooting
# path; the consumer is deliberately read-only (no observation point exists once
# the generator never touches buf again), so it is intentionally parity-blind.
def no_later_read() -> Iterator[list[int]]:
    buf: list[int] = []
    buf.append(7)
    yield buf                     # tpyc: ok


def read_once() -> None:
    for v in no_later_read():
        print("once:", v)


def main() -> None:
    mutate_observe()
    walk_prune()
    read_once()


main()
