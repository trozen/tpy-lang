# os.walk(topdown=False): bottom-up post-order (each dir after its subdirs).
# Output is host-independent: rows sorted (scandir sibling order is OS-arbitrary)
# and the post-order property asserted structurally via order_ok.
import os
from typing import Callable
from tpy import Own, readonly


def report(e: readonly[OSError]) -> None:
    print("onerror fired")


def walk_rows(root: str) -> Own[list[str]]:
    rows: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        rel = dirpath[len(root):]
        if len(rel) == 0:
            rel = "."
        dn = list(dirnames)
        dn.sort()
        rows.append(rel + " dirs=" + str(dn) + " files=" + str(len(filenames)))
    rows.sort()
    return rows


# Bottom-up order holds: every directory is yielded AFTER its parent's other
# subtrees but BEFORE the parent itself. Sibling order within a level is
# scandir-arbitrary, so assert only the structural invariant: each dir's parent
# appears later in the sequence (post-order).
def order_ok(root: str) -> bool:
    seen: list[str] = []
    for dp, dn, fn in os.walk(root, topdown=False):
        seen.append(dp)
    i = 0
    while i < len(seen):
        cur = seen[i]
        if cur != root:
            parent = os.path.dirname(cur)
            parent_idx = -1
            j = 0
            while j < len(seen):
                if seen[j] == parent:
                    parent_idx = j
                j += 1
            if parent_idx <= i:
                return False
        i += 1
    return True


def yields(top: str, cb: Callable[[readonly[OSError]], None] | None) -> int:
    n = 0
    for dirpath, dirnames, filenames in os.walk(top, topdown=False, onerror=cb):
        n += 1
    return n


def build(root: str) -> None:
    os.mkdir(root)
    os.mkdir(root + "/a")
    os.mkdir(root + "/a/a1")
    os.mkdir(root + "/b")
    with open(root + "/top.txt", "w") as f:
        f.write("t")
    with open(root + "/a/x.txt", "w") as f:
        f.write("x")


def teardown(root: str) -> None:
    os.remove(root + "/a/x.txt")
    os.remove(root + "/top.txt")
    os.rmdir(root + "/a/a1")
    os.rmdir(root + "/a")
    os.rmdir(root + "/b")
    os.rmdir(root)


def main() -> None:
    root = os.path.realpath("/tmp") + "/tpy_oswalk_bu_tree"
    if os.path.exists(root):
        teardown(root)
    build(root)

    for r in walk_rows(root):
        print(r)
    print("post-order ok:", order_ok(root))
    teardown(root)

    missing = "/tmp/tpy_oswalk_bu_missing"
    # onerror fires once on the unscannable top, then 0 yields.
    n1 = yields(missing, report)
    print("named yields:", n1)
    # default onerror=None: silent skip.
    n2 = yields(missing, None)
    print("default yields:", n2)


main()
