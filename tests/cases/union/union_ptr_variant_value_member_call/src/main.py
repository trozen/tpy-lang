# A pointer-variant union local reseated with an owned-buffer MEMBER rvalue
# (bytes / str) produced by a call or a method call: the value-variant slot
# copies whatever the callee returns, then the local re-lifts to pointers.
from tpy import Own


def make_bytes(s: str) -> Own[bytes]:
    return s.encode()


def make_str(s: str) -> Own[str]:
    return s + "!"


def show(v: bytes | dict[str, str] | None) -> str:
    if v is None:
        return "none"
    if isinstance(v, bytes):
        return str(len(v))
    return "dict"


def main():
    body: bytes | dict[str, str] | None = None
    print(show(body))
    # A METHOD-call rvalue at a bytes member slot.
    body = "ab".encode()
    print(show(body))
    # A FREE-call rvalue at the same slot.
    body = make_bytes("cde")
    print(show(body))

    text: str | dict[str, str] | None = None
    # The str member: the variant member spells the owned std::string, so
    # a view-returning method still copies into the slot.
    text = make_str("x")
    if isinstance(text, str):
        print(text)
    text = "hi".upper()
    if isinstance(text, str):
        print(text)

    # The dict member is a reference type: mutate through the union binding
    # and read it back off the source, so a silent copy would show.
    d = {"k": "v"}
    holder: bytes | dict[str, str] | None = d
    if isinstance(holder, dict):
        holder["k"] = "w"
    print(d["k"])


main()
