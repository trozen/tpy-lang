# A recursive-union WRAPPER result (`json.loads`) bound to a local that is
# assigned again: the wrapper is a plain value slot, so the second assignment
# is an ordinary value assign -- no slot or pointer to rebind, which is what
# the container family beside it needs. One section per position; each one
# reads the SECOND value so a decl that kept the first would show.
from typing import Iterator

import asyncio
import json


# free function
def in_function() -> None:
    v = json.loads("[1]")  # tpyc: ok
    print("function", json.dumps(v))
    v = json.loads("[2, 3]")
    print("function", json.dumps(v))


class Holder:
    # constructor
    def __init__(self) -> None:
        v = json.loads("{}")  # tpyc: ok
        v = json.loads('{"a": 1}')
        print("constructor", json.dumps(v))

    # method
    def run(self) -> None:
        v = json.loads("[]")  # tpyc: ok
        v = json.loads("[4]")
        print("method", json.dumps(v))


# generator -- the frame keeps its own pointer slot for the same rebind
def in_generator() -> Iterator[str]:
    v = json.loads("[1]")  # tpyc: ok
    v = json.loads("[5, 6]")
    yield json.dumps(v)


# async
async def in_async() -> None:
    v = json.loads("[1]")  # tpyc: ok
    v = json.loads("[7]")
    print("async", json.dumps(v))


# closure
def in_closure() -> None:
    def inner() -> None:
        v = json.loads("[1]")  # tpyc: ok
        v = json.loads("[8]")
        print("closure", json.dumps(v))

    inner()


def main() -> None:
    in_function()
    h = Holder()
    h.run()
    for s in in_generator():
        print("generator", s)
    asyncio.run(in_async())
    in_closure()


main()
