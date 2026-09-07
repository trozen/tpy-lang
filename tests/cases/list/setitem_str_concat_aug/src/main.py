# `+=` on a str ELEMENT target: a read-modify-write pair over the resolved str
# concat, not the in-place append a str NAME target takes.
def bump(ys: list[str], t: dict[str, str], k: str) -> None:
    ys[0] += "a"  # element target -> read, concat, write back
    t["x"] += k


def main() -> None:
    ys = ["q"]
    t = {"x": "y"}
    bump(ys, t, "z")
    print(ys[0], t["x"])


main()
