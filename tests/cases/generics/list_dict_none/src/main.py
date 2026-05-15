# list[None] / dict[K, None] / tuple[None, T] -- builtin containers
# with the unit type at type-arg position. dict[K, None] is the
# CPython "set of K, with insertion order" idiom; list[None] is rare
# but well-defined (e.g. fixed-shape placeholders).
def main() -> None:
    xs: list[None] = []
    xs.append(None)
    xs.append(None)
    print("list len:", len(xs))

    d: dict[str, None] = {}
    d["a"] = None
    d["b"] = None
    print("dict size:", len(d))
    print("has a:", "a" in d)

    t: tuple[None, int] = (None, 42)
    print("tuple snd:", t[1])


main()
