# String literals with embedded \x00 -- the C++ string_view ctor uses
# strlen for raw const-char-pointer literals, so codegen must use the
# explicit-length form to preserve the full byte run.
def take_str(s: str) -> int:
    return len(s)

def use_default(s: str = "\x00null") -> int:
    return len(s)

def main() -> None:
    s = "\x00null"
    print(len(s))
    print(s == "\x00null")
    print(take_str("ab\x00cd"))
    print(use_default())

    # match on a NUL-containing literal
    target = "\x00x"
    match target:
        case "\x00x":
            print("matched")
        case _:
            print("no match")

    # f-string pure literal with NUL
    pure = f"a\x00b"
    print(len(pure))

    # f-string with interpolation -- routes through std::vformat when NUL
    # appears in any literal portion (std::format's consteval ctor would
    # truncate via strlen).
    name = "world"
    interp = f"a\x00b{name}"
    print(len(interp))
    print(interp == "a\x00bworld")

main()
