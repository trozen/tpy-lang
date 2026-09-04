# A walrus whose target is a value-repr `str | None` / `bytes | None` binds the
# bare `std::optional<...>` slot and None-tests it with has_value, the owned-view
# twin of the Optional[scalar] walrus.
from tpy import Int32


def maybe_text(k: Int32) -> str | None:
    if k > 0:
        return "abc"
    return None


def maybe_blob(k: Int32) -> bytes | None:
    if k > 0:
        return b"ab"
    return None


def text_len(k: Int32) -> Int32:
    if (s := maybe_text(k)) is not None:   # tpyc: ok -- `str | None` target
        print(s)
        return len(s)
    return -1


def blob_len(k: Int32) -> Int32:
    if (b := maybe_blob(k)) is not None:   # tpyc: ok -- `bytes | None` target
        return len(b)
    return -1


def from_text_param(t: str | None) -> Int32:
    # A PARAM source binds the VIEW form, so the owned slot takes an explicit
    # copy rather than the optional's converting assignment.
    if (s := t) is not None:               # tpyc: ok -- a `str | None` param
        return len(s)
    return -1


def from_blob_param(t: bytes | None) -> Int32:
    # The bytes face of the same copy: `optional<span>` never converts to
    # `optional<vector>` on its own.
    if (b := t) is not None:               # tpyc: ok -- a `bytes | None` param
        return len(b)
    return -1


def reassigned(k: Int32) -> Int32:
    s = maybe_text(k)
    if s is None:
        # A REUSE of the same target assigns the predeclared slot in place.
        if (s := maybe_text(1)) is not None:  # tpyc: ok
            return len(s)
    return 0


def main() -> None:
    print(text_len(1), text_len(0))
    print(blob_len(1), blob_len(0))
    print(from_text_param("abcd"), from_text_param(None))
    print(from_blob_param(b"abc"), from_blob_param(None))
    print(reassigned(0))


main()
