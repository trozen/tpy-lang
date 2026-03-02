# Dict literal, subscript read/write, len, print
def main() -> None:
    d = {"x": 1, "y": 2, "z": 3}
    print(d)
    print(d["x"])
    print(d["y"])
    print(len(d))
    d["w"] = 4
    print(d["w"])
    print(len(d))

main()
