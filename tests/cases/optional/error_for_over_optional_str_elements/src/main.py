# A for-loop element of type Optional[str]: the view inner is outside the
# admitted cheap-scalar element family, so the loop head rejects. The
# Optional[scalar] element that IS admitted is pinned by
# tests/cases/none_safety/loop_foreach_optional_body_narrowing.


def joined(items: list[str | None]) -> str:
    out = ""
    for s in items:  # tpyc: error(/foreach\.elem_family\.optional/)
        if s is None:
            continue
        out = out + s
    return out


def main() -> None:
    print(joined(["a", None, "bb"]))


main()
