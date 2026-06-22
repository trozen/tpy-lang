# Shared os types with no module-level executable code, so os.path can import
# them without forming an executable-bearing import cycle with the os package
# (TPy rejects a cyclic import of a module that runs top-level statements).
from tpy import Int64


# os.stat / lstat result. Field names match CPython so user code (and the cpy
# test phase, which sees the real os.stat_result) is portable. Native code
# returns a flat tuple (the @native helper cannot construct this TPy type); the
# wrapper unpacks it. Attribute access only -- unlike CPython's structseq, this
# does not support the sequence protocol (st[0], len, iteration); those are a
# clean compile error, not a silent divergence.
class stat_result:
    def __init__(self, mode: Int64, ino: Int64, dev: Int64, nlink: Int64,
                 uid: Int64, gid: Int64, size: Int64,
                 atime: float, mtime: float, ctime: float,
                 atime_ns: Int64, mtime_ns: Int64, ctime_ns: Int64) -> None:
        self.st_mode = mode
        self.st_ino = ino
        self.st_dev = dev
        self.st_nlink = nlink
        self.st_uid = uid
        self.st_gid = gid
        self.st_size = size
        self.st_atime = atime
        self.st_mtime = mtime
        self.st_ctime = ctime
        self.st_atime_ns = atime_ns
        self.st_mtime_ns = mtime_ns
        self.st_ctime_ns = ctime_ns
