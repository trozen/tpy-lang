# A deduced str/bytes view local in a resumable frame is promoted to owned
# storage (frame fields outlive case-block temps), while a str literal
# source keeps the zero-copy view and an explicit StrView annotation keeps
# the user's view contract.
from typing import Iterator
from tpy import Int32, StrView


def pair(n: Int32) -> tuple[str, str]:
    return ("host-" + str(n), "port-" + str(n))


def unpack_across_yield() -> Iterator[Int32]:
    # The tuple temp dies at the first yield; the promoted owned fields
    # must survive to the post-suspension read.
    host, port = pair(7)
    yield 1
    print(host, port)
    yield 2


def dict_keys(d: dict[str, Int32]) -> Iterator[str]:
    # A dict-key loop var yielded into an owned Iterator[str] slot: the
    # promoted local converts cleanly where a view field failed the build.
    for k in d:
        yield k
    yield "end"


def blob_slices(blobs: list[bytes]) -> Iterator[Int32]:
    # bytes sibling: a container-element view local crossing a yield.
    for b in blobs:
        yield len(b)
    total = blobs[0]  # tpyc: type(bytes)
    yield 100
    yield len(total)


def static_sources() -> Iterator[Int32]:
    # A literal-sourced local has static storage: stays a zero-copy view.
    lit = "static"  # tpyc: type(StrView)
    view: StrView = "explicit"  # tpyc: type(StrView)
    yield 1
    print(lit, view)


def main() -> None:
    for v in unpack_across_yield():
        print(v)
    d = {"a": 1, "b": 2}
    for k in dict_keys(d):
        print(k)
    for n in blob_slices([b"xy", b"z"]):
        print(n)
    for v in static_sources():
        print(v)


main()
