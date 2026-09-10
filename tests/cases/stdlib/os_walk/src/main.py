# os.walk v1: topdown prune, followlinks, broken/missing-dir skip. Output is
# host-independent (relative paths, sorted -- scandir order is OS-arbitrary).
from tpy import Int32
import os


def build(root: str) -> None:
    os.mkdir(root)
    os.mkdir(root + "/sub1")
    os.mkdir(root + "/sub1/deep")
    os.mkdir(root + "/sub2")
    os.mkdir(root + "/skip")
    with open(root + "/top.txt", "w") as f:
        f.write("t")
    with open(root + "/sub1/a.txt", "w") as f:
        f.write("a")
    with open(root + "/sub1/deep/b.txt", "w") as f:
        f.write("b")
    with open(root + "/skip/junk.txt", "w") as f:
        f.write("j")
    os.symlink(root + "/sub1", root + "/lnk")
    # a broken symlink -- is_dir() raises; walk must treat it as a non-dir
    # (it lands in filenames), not crash.
    os.symlink(root + "/does_not_exist", root + "/dangling")


def teardown(root: str) -> None:
    os.remove(root + "/dangling")
    os.remove(root + "/lnk")
    os.remove(root + "/skip/junk.txt")
    os.remove(root + "/sub1/deep/b.txt")
    os.remove(root + "/sub1/a.txt")
    os.remove(root + "/top.txt")
    os.rmdir(root + "/skip")
    os.rmdir(root + "/sub2")
    os.rmdir(root + "/sub1/deep")
    os.rmdir(root + "/sub1")
    os.rmdir(root)


def walk_sorted(root: str) -> None:
    rows: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        kept: list[str] = []
        for d in dirnames:
            if d != "skip":
                kept.append(d)
        dirnames[:] = kept
        files = sorted(filenames)
        rel = dirpath[len(root):]
        if len(rel) == 0:
            rel = "."
        rows.append(rel + " files=" + ",".join(files))
    rows.sort()
    for r in rows:
        print(r)


def count_dirs(root: str, follow: bool) -> Int32:
    n = 0
    for dp, dn, fn in os.walk(root, followlinks=follow):
        n += 1
    return n


def prune_all(root: str) -> Int32:
    # dirnames[:] = [] stops all descent -- only the root tuple is yielded.
    n = 0
    for dp, dn, fn in os.walk(root):
        empty: list[str] = []
        dn[:] = empty
        n += 1
    return n


# Walk pre-order, unsorted, so a sibling/parent-before-child order regression is
# visible (the host-independent rows.sort() in walk_sorted would mask it). Order
# within a level is scandir-arbitrary, so assert only the structural invariant:
# every dir is visited after its parent (parent index < child index).
def order_ok(root: str) -> bool:
    seen: list[str] = []
    for dp, dn, fn in os.walk(root):
        seen.append(dp)
    i = 0
    while i < len(seen):
        cur = seen[i]
        parent = os.path.dirname(cur)
        if cur != root:
            j = 0
            found = False
            while j < i:
                if seen[j] == parent:
                    found = True
                j += 1
            if not found:
                return False
        i += 1
    return True


def main() -> None:
    tmp = os.getcwd()
    root = tmp + "/tpy_oswalk_case"
    if os.path.exists(root):
        teardown(root)
    build(root)
    walk_sorted(root)
    # followlinks: the lnk -> sub1 symlink is descended only when followlinks
    # (so the count is higher); no prune here.
    print("nofollow dirs:", count_dirs(root, False))
    print("follow dirs:", count_dirs(root, True))
    # missing root -> default onerror=None skips, yields nothing
    n = 0
    for dp, dn, fn in os.walk(tmp + "/tpy_oswalk_missing"):
        n += 1
    print("missing yields:", n)
    # prune-to-empty stops all descent (only root yielded)
    print("prune-all yields:", prune_all(root))
    # walking a file (not a dir): scandir raises -> skipped, yields nothing
    nf = 0
    for dp, dn, fn in os.walk(root + "/top.txt"):
        nf += 1
    print("file-as-top yields:", nf)
    # parent-before-child order holds (unsorted, not masked by walk_sorted)
    print("order ok:", order_ok(root))
    teardown(root)


main()
