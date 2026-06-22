# os.path.commonpath: component-wise longest common path. Empty list or a mix
# of absolute and relative paths raises ValueError, matching CPython.
import os


def main() -> None:
    print(os.path.commonpath(["/a/b/c", "/a/b/d", "/a/b/e/f"]))
    print(os.path.commonpath(["x/y/z", "x/y/w"]))
    print(os.path.commonpath(["/a", "/a/b"]))
    print(os.path.commonpath(["/usr/lib", "/usr/local/lib"]))
    print(os.path.commonpath(["one"]))
    print(os.path.commonpath(["a/./b", "a/b/c"]))
    # absolute paths sharing only the root -> "/"; relative with nothing
    # in common -> "" (the two empty-common-component branches).
    print("[" + os.path.commonpath(["/a/b", "/c/d"]) + "]")
    print("[" + os.path.commonpath(["a/b", "c/d"]) + "]")
    try:
        empty: list[str] = []
        os.path.commonpath(empty)
    except ValueError:
        print("empty ValueError")
    try:
        os.path.commonpath(["/abs", "rel"])
    except ValueError:
        print("mixed ValueError")


main()
