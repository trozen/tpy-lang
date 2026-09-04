# A `Span[str]` local: an owned-str span element decls as the plain spelled
# copy and reads bare, exactly as the same element does off a list.
from tpy import Int32, Span


def first(words: Span[str]) -> str:
    return words[0]


def main() -> None:
    src: list[str] = []
    src.append("alpha")
    src.append("beta")
    src.append("gamma")
    tail = src[1:]  # tpyc: type(/Span/)
    print(len(tail), tail[0], tail[1])
    print(first(tail))
    # A span element assigns through to the source; read it back off the span
    # so the check stays inside the view (CPython's slice copies, so reading
    # the LIST back would diverge).
    tail[0] = "BETA"
    print(tail[0], first(tail))


main()
