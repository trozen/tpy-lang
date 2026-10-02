# Iterating an open text file yields its lines, each keeping its newline (the
# last one only if the file ends with one), in a for statement, a
# comprehension, a generator and an async body, and through list / enumerate /
# zip / a generator expression; iteration continues from wherever earlier
# reads stopped.
import asyncio
from typing import Iterator


class Log:
    path: str

    def __init__(self, path: str) -> None:
        self.path = path

    def count(self) -> int:
        # method
        n = 0
        with open(self.path) as fh:
            for line in fh:  # tpyc: ok
                n += 1
        return n


def stripped(path: str) -> Iterator[str]:
    # generator body: the file loop spans the yields
    fh = open(path)
    for line in fh:  # tpyc: ok
        yield line.strip()
    fh.close()


async def total_length(path: str) -> int:
    # async body: the file loop spans the awaits
    n = 0
    fh = open(path)
    for line in fh:  # tpyc: ok
        await asyncio.sleep(0)
        n += len(line)
    fh.close()
    return n


def count_lines(path: str) -> int:
    # function: a counting loop
    n = 0
    with open(path) as fh:
        for line in fh:  # tpyc: ok
            n += 1
    return n


def main() -> None:
    with open("t.txt", "w") as fh:
        fh.write("alpha\n\nbeta\nlast")
    # for statement; the name is bound again by each with block
    with open("t.txt") as fh:
        for line in fh:  # tpyc: ok
            print("for:", repr(line))
    # after a readline, iteration yields the rest; lines stored past the loop
    kept: list[str] = []
    with open("t.txt") as fh:
        first = fh.readline()
        for line in fh:  # tpyc: ok
            kept.append(line.strip())
    print("resume:", first.strip(), kept)
    # comprehension
    with open("t.txt") as fh:
        print("comprehension:", [len(line) for line in fh])  # tpyc: ok
    print("function:", count_lines("t.txt"))
    print("method:", Log("t.txt").count())
    print("generator:", list(stripped("t.txt")))
    print("async:", asyncio.run(total_length("t.txt")))
    # the generic consumers of an iterable
    with open("t.txt") as fh:
        print("list:", list(fh))  # tpyc: ok
    with open("t.txt") as fh:
        for i, line in enumerate(fh):  # tpyc: ok
            print("enumerate:", i, repr(line))
    with open("t.txt") as fh:
        print("genexpr:", sum(len(line) for line in fh))  # tpyc: ok
    with open("t.txt") as left:
        with open("t.txt") as right:
            print("zip:", [a == b for a, b in zip(left, right)])  # tpyc: ok
    # a consumer that stops early leaves the file at the next unread line
    with open("t.txt") as fh:
        for i, line in enumerate(fh):  # tpyc: ok
            if i == 0:
                break
        print("resume_after_enumerate:", repr(fh.readline()))
    # one file iterated twice at once: the two iterators take lines in turn
    with open("t.txt") as fh:
        print("zip_same_file:", [a + b for a, b in zip(fh, fh)])  # tpyc: ok
    # a generator abandoned midway has read nothing past its last line
    with open("t.txt") as fh:
        print("any:", any(len(line) == 1 for line in fh), repr(fh.readline()))
    # an empty file yields nothing
    with open("empty.txt", "w") as fh:
        pass
    with open("empty.txt") as fh:
        for line in fh:
            print("empty: never")
    # a second loop over an exhausted file yields nothing
    with open("t.txt") as fh:
        n = 0
        for line in fh:
            n += 1
        for line in fh:
            n += 100
    print("exhausted:", n)


main()

# module level
with open("t.txt") as top:
    for line in top:  # tpyc: ok
        print("module:", line.strip())
