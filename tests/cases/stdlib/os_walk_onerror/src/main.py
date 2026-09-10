# os.walk onerror: provided-but-never-fires on a real tree, fires on a scandir
# failure (missing top), aborts the walk when it raises; named + lambda forms.
import os
from typing import Callable
from tpy import Int32, Own, readonly


def report(e: readonly[OSError]) -> None:
    # Avoid str(e): TPy's OSError.__str__ format differs from CPython (BUGS.md).
    print("onerror fired")


def boom(e: readonly[OSError]) -> None:
    raise RuntimeError("stop")


def yields(top: str, cb: Callable[[readonly[OSError]], None] | None) -> Int32:
    n = 0
    for dirpath, dirnames, filenames in os.walk(top, onerror=cb):
        n += 1
    return n


def walk_rows(root: str, cb: Callable[[readonly[OSError]], None] | None) -> Own[list[str]]:
    rows: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=cb):
        rel = dirpath[len(root):]
        if len(rel) == 0:
            rel = "."
        rows.append(rel + " files=" + str(len(filenames)))
    rows.sort()
    return rows


def build(root: str) -> None:
    os.mkdir(root)
    os.mkdir(root + "/sub")
    with open(root + "/top.txt", "w") as f:
        f.write("t")
    with open(root + "/sub/a.txt", "w") as f:
        f.write("a")


def teardown(root: str) -> None:
    os.remove(root + "/sub/a.txt")
    os.remove(root + "/top.txt")
    os.rmdir(root + "/sub")
    os.rmdir(root)


def main() -> None:
    tmp = os.getcwd()
    root = tmp + "/tpy_oswalk_onerror_tree"
    if os.path.exists(root):
        teardown(root)
    build(root)

    for r in walk_rows(root, report):
        print("named:", r)
    for r in walk_rows(root, lambda e: print("never")):
        print("lambda:", r)
    teardown(root)

    missing = "tpy_oswalk_onerror_missing"

    # Count is computed before printing -- a side-effecting print arg would
    # interleave differently under TPy's cout-chain lowering vs CPython.
    n1 = yields(missing, report)
    print("named yields:", n1)

    n2 = yields(missing, lambda e: print("lam fired"))
    print("lambda yields:", n2)

    n3 = yields(missing, None)
    print("default yields:", n3)

    try:
        for dp, dn, fn in os.walk(missing, onerror=boom):
            print("yielded")
        print("no raise")
    except RuntimeError:
        print("aborted by onerror")


main()
