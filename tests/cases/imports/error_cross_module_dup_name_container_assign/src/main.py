# A same-short-name collision nested in a container is qualified at the leaf
# (`list[red.Tag]` / `list[blue.Tag]`), not just bare top-level records.
from red import Tag
from blue import Tag as BlueTag


def main() -> None:
    a: list[Tag] = []
    b: list[BlueTag] = []
    a = b  # tpyc: error(/expected list\[red.Tag\], got list\[blue.Tag\]/)
    print(len(a))


main()
