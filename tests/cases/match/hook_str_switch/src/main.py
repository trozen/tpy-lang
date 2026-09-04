# A str-literal `match` whose arms suspend: the discriminator switch carries
# the resumable dispatch hook, so the arm bodies are frame blocks the skeleton
# walks -- guarded prefix arms and a trailing capture included.
import asyncio
from typing import Iterator


def gen(s: str, flag: bool) -> Iterator[str]:
    match s:
        case "z" if flag:  # a guarded prefix arm, emitted before the switch
            yield "guarded"
        case "a":
            yield "1"
        case "b":
            yield "2"
        case "c":
            yield "3"
        case "d":
            yield "4"
        case "e":
            yield "5"
        case other:  # a trailing capture, bound into the frame
            yield "other:" + other


async def acoro(s: str) -> str:
    match s:
        case "a":
            # A suspension inside a bucket arm: the resume state belongs to
            # the arm's own block, not the dispatch.
            await asyncio.sleep(0)
            return "A"
        case "bb":
            await asyncio.sleep(0)
            return "B"
        case "ccc":
            return "C"
        case "dddd":
            return "D"
        case "eeeee":
            return "E"
        case _:
            await asyncio.sleep(0)
            return "?"


async def amain() -> None:
    print(await acoro("a"))
    print(await acoro("ccc"))
    print(await acoro("zz"))


def main() -> None:
    for v in gen("z", True):
        print(v)
    for v in gen("z", False):
        print(v)
    for v in gen("c", False):
        print(v)
    asyncio.run(amain())


main()
