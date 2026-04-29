# repr() of strings produces Python-faithful escape form: outer quotes
# plus C-style escapes for control characters, backslash, and the active
# quote. Quote selection follows CPython: prefer single quotes, switch to
# double when the string contains `'` and no `"`. The same escaping is
# used by container printing (print([s]), print({s})).
def main() -> None:
    # Control characters
    print(repr("a\nb"))         # 'a\nb'
    print(repr("tab\there"))    # 'tab\there'
    print(repr("cr\rfoo"))      # 'cr\rfoo'

    # Backslash
    print(repr("back\\slash"))  # 'back\\slash'

    # Quote selection
    print(repr("plain"))        # 'plain'
    print(repr("can't"))        # "can't" (switches to double)
    print(repr("dq\"x"))        # 'dq"x' (keeps single)
    print(repr("'and\""))       # '\'and"' (both present, escape `'`)

    # Containers: print_element uses the same escaping
    items: list[str] = []
    items.append("a\nb")
    items.append("c")
    items.append("can't")
    print(items)

    # Dict with strings
    d: dict[str, str] = {}
    d["key"] = "v\tab"
    print(d)

    # Set with strings (set printer routes through the same print_element).
    s: set[str] = set()
    s.add("only-one\n")
    print(s)

    # repr() on Optional[str] (parameter form -- can't be narrowed by sema).
    show_optional("a\nb")
    show_optional(None)

    # Edge case: empty string.
    print(repr(""))


def show_optional(s: str | None) -> None:
    print(repr(s))


main()
