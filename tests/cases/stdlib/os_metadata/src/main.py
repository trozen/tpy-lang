# File metadata: chmod (+ verify via stat), access (R/W/X/F_OK), utime
# (+ verify mtime), chown-to-self (no-op, always permitted). Byte-compared
# against CPython.
import os


def main():
    p = "/tmp/tpy_os_metadata"
    if os.path.exists(p):
        os.remove(p)
    with open(p, "w") as fh:
        fh.write("data")

    os.chmod(p, 0o640)
    print("mode", oct(os.stat(p).st_mode & 0o777))   # 0o640
    print("access", os.access(p, os.F_OK), os.access(p, os.R_OK),
          os.access(p, os.W_OK), os.access(p, os.X_OK))   # True True True False
    print("missing", os.access("/tmp/tpy_nope_xyz", os.F_OK))   # False

    os.utime(p, (1000000000.0, 1500000000.0))
    st = os.stat(p)
    print("times", st.st_atime == 1000000000.0, st.st_mtime == 1500000000.0)

    os.chown(p, st.st_uid, st.st_gid)   # to self -> permitted no-op
    print("chown ok")

    os.remove(p)


main()
