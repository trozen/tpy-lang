# copy() of a builtin-container subclass keeps its parts and of an exception
# its state, under CPython as under TPy: every copy is a new object, and a
# write through it never reaches the source.
from tpy import int32, copy, Own


class L(list[int32]):
    pass


class D(dict[str, int32]):
    pass


class S(set[int32]):
    pass


class CodeErr(Exception):
    def __init__(self, code: int32, msg: str) -> None:
        super().__init__(msg)
        self.code = code


class PathErr(OSError):
    def __init__(self, path: str) -> None:
        super().__init__(2, "missing")
        self.path = path


class Hooked:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __copy__(self) -> Own["Hooked"]:
        return Hooked(self.n + 100)


class HookErr(Exception):
    def __init__(self, h: Own[Hooked]) -> None:
        super().__init__("hook")
        self.h = h


def main() -> None:
    a = L()
    a.append(1)
    a.append(2)
    d = D()
    d["k"] = 1
    s = S()
    s.add(3)
    # each copy is a new container holding the same values
    b = copy(a)  # tpyc: ok
    e = copy(d)  # tpyc: ok
    t = copy(s)  # tpyc: ok
    print("cpy identity:", b is not a, e is not d, t is not s)
    b.append(4)
    e["k"] = 9
    t.add(5)
    print("cpy subclass source:", len(a), d["k"], len(s))
    print("cpy subclass copy:", len(b), e["k"], len(t))
    # an exception's constructor may take other arguments: its state is
    # copied without calling it
    err = CodeErr(7, "boom")
    f = copy(err)  # tpyc: ok
    f.code = 8
    print("cpy exception:", err.code, f.code, str(f))
    # an OSError's errno and strerror come with its arguments
    o = FileNotFoundError(2, "nope")
    p = copy(o)  # tpyc: ok
    print("cpy oserror:", p.errno, p.strerror, str(p))
    # an OSError's filename, and an errno set after construction, are kept
    o3 = FileNotFoundError(2, "nope", "a.txt")
    p3 = copy(o3)  # tpyc: ok
    print("cpy oserror filename:", p3.filename, str(p3))
    o3.errno = 5
    p4 = copy(o3)  # tpyc: ok
    print("cpy oserror errno:", p4.errno)
    # an OSError subclass with its own constructor keeps its errno,
    # strerror and fields
    pe = PathErr("a.txt")
    pc = copy(pe)  # tpyc: ok
    pc.path = "b.txt"
    print("cpy oserror subclass:", pc.errno, pc.strerror, pc.path, pe.path,
          str(pc))
    # a SystemExit keeps its code
    sx = SystemExit(2)
    c = copy(sx)  # tpyc: ok
    print("cpy systemexit:", c.code)
    # fixed-width numbers keep their values through copy() (spelled
    # explicitly: under CPython only `int32(...)` builds the stub's int32)
    nums = [int32(42), int32(7)]
    nc = copy(nums)  # tpyc: ok
    print("cpy fixed ints:", nc[0], nc[1])
    # a field with its own __copy__ is copied through it
    h = HookErr(Hooked(1))
    k = copy(h)  # tpyc: ok
    print("cpy field hook:", h.h.n, k.h.n)
