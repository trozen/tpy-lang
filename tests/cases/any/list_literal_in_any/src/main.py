# Storing an unannotated list literal in Any. The literal's element
# type defaults to the configured default_int (int32); the cell stores
# `list<int32>` rather than reaching codegen with an unresolved
# PendingListType.

from typing import Any


def main() -> None:
    a: Any = [1, 2, 3]
    print("ok")


main()
