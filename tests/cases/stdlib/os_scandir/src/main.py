# os.scandir + DirEntry (name / is_dir / is_file / is_symlink / stat). Builds a
# fresh /tmp tree (subdir, a 2-byte file, a symlink to it) so both phases start
# clean; entries are sorted since scandir order is unspecified. The symlink case
# exercises the d_type -> stat follow path (is_file follows, is_symlink doesn't).
# Byte-compared against CPython.
import os


def teardown(base: str) -> None:
    if os.path.lexists(base + "/lnk"):
        os.remove(base + "/lnk")
    if os.path.exists(base + "/f.txt"):
        os.remove(base + "/f.txt")
    if os.path.exists(base + "/sub"):
        os.rmdir(base + "/sub")
    if os.path.exists(base + "/empty"):
        os.rmdir(base + "/empty")
    if os.path.exists(base):
        os.rmdir(base)


def main():
    base = "/tmp/tpy_os_scandir"
    teardown(base)
    os.mkdir(base)
    os.mkdir(base + "/sub")
    os.mkdir(base + "/empty")
    with open(base + "/f.txt", "w") as fh:
        fh.write("hi")
    os.symlink(base + "/f.txt", base + "/lnk")

    rows: list[str] = []
    size = 0
    for e in os.scandir(base):
        rows.append(e.name + ":" + str(e.is_dir()) + "/" + str(e.is_file())
                    + "/" + str(e.is_symlink()))
        if e.name == "f.txt":
            size = e.stat().st_size      # DirEntry.stat() -> stat_result
    rows.sort()
    for r in rows:
        print(r)
    print("f.txt size:", size)           # 2 ("hi")

    # empty directory -> empty listing
    n = 0
    for _ in os.scandir(base + "/empty"):
        n += 1
    print("empty entries:", n)

    teardown(base)


main()
