# Returning a VIEW-form str/bytes source at an OWNED-inner Optional return
# (`def b(s: str) -> str | None: return s`, where the param is a
# `std::string_view` and the slot an `std::optional<std::string>`): the render
# materializes the view into the owned inner. The copy is required -- the
# return slot outlives the caller's buffer, and
# `std::optional<std::string>`'s converting constructor from a view is
# EXPLICIT -- and it is unobservable, since str and bytes are value types. An
# async body rejects one rung on (res.return_type), so the coroutine position
# is not covered here.
from tpy import int32


class Tagger:
    prefix: str

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    # method position
    def tag(self, s: str) -> str | None:
        if len(s) == 0:
            return None
        return s  # tpyc: ok


# free function: a str param at an `str | None` return
def str_position(s: str) -> str | None:
    return s  # tpyc: ok


# the bytes twin: a `bytes` param is a BytesView at the signature
def bytes_position(b: bytes) -> bytes | None:
    return b  # tpyc: ok


def main() -> None:
    r = str_position("hello")
    print("str:", -1 if r is None else len(r))
    rb = bytes_position(b"xyz")
    print("bytes:", -1 if rb is None else len(rb))
    t = Tagger("p").tag("abc")
    print("method:", -1 if t is None else len(t))
    e = Tagger("p").tag("")
    print("method empty:", -1 if e is None else len(e))


main()
