# Slice-object ctor rvalues passed as call args: basic_slice(1, 3) / slice(...)
# expand to ::tpy::BasicSlice{...} / ::tpy::Slice{...} directly in the arg slot.
from tpy import basic_slice


def cut(text: str, sl: basic_slice) -> str:
    return text[sl]


def pick(text: str, st: slice) -> str:
    return text[st]


def run(text: str) -> None:
    a = cut(text, basic_slice(1, 3))
    b = cut(text, basic_slice(2, None))
    print(a, b)
    c = pick(text, slice(0, 7, 2))
    d = pick(text, slice(None, None, -1))
    print(c, d)


def main() -> None:
    run("greetings")


main()
