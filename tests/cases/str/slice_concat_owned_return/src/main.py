# A `String` local returned at an owned `str` slot: the passthrough coerce must
# carry the wrapped local's STORAGE form, not the value default.
def cut_join(s: str) -> str:
    t = s[1:3] + s
    return t


def fmt_slice(s: str) -> str:
    return f"mid={s[1:3]} c={s[0] == 'h'}"


def iter_slice(s: str) -> None:
    v = s[1:]
    for c in v:
        print(c)


def main() -> None:
    s = "hello"
    print(cut_join(s), fmt_slice(s))
    iter_slice(s)


main()
