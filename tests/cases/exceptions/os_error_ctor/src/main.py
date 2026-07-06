# CPython's OSError(errno, strerror[, filename]) constructor forms: exact
# str(e) formatting ("[Errno N] strerror[: 'filename']"), attribute
# population, subclass inheritance of the ctors, and the __new__-style errno
# -> subclass mapping surfacing at RAISE time (@virtual_raise: both the
# direct `raise OSError(...)` and bound-then-raise forms map; post-hoc
# attribute assignment and explicit subclasses never re-map, like CPython).


def refuse() -> None:
    raise ConnectionRefusedError(111, "Connection refused")


def direct_mapped() -> None:
    raise OSError(5, "Input/output error")  # EIO maps to no subclass


def direct_mapped_enoent() -> None:
    raise OSError(2, "No such file or directory")


def bound_mapped() -> None:
    e = OSError(17, "File exists")
    raise e


def posthoc_plain() -> None:
    e = OSError("plain")
    e.errno = 2
    raise e


def main() -> None:
    e = OSError(2, "No such file or directory")
    print(e)
    print(e.errno, e.strerror)
    f = OSError(13, "Permission denied", "/etc/shadow")
    print(f)
    print(f.errno, f.strerror, f.filename)
    # Message-only form keeps its plain text; errno stays unset (TPy 0 /
    # CPython None -- both falsy, the declared no-Optional-attrs divergence).
    g = OSError("plain msg")
    print(g, not g.errno)
    # Subclass ctors are CPython-exact; an explicit subclass keeps its type.
    h = FileNotFoundError(2, "No such file or directory", "x.txt")
    print(h)
    try:
        refuse()
    except ConnectionError as exc:
        print(exc)
        print(exc.errno)
    try:
        raise FileExistsError(17, "File exists", "out.txt")
    except OSError as exc:
        print(exc, exc.filename)
    # Zero-arg form (a distinct overload).
    z = OSError()
    print(str(z) == "", not z.errno)
    # The errno -> subclass mapping at raise time, in every shape:
    try:
        direct_mapped_enoent()
    except FileNotFoundError as exc:
        print("direct mapped:", exc)
    try:
        bound_mapped()
    except FileExistsError as exc:
        print("bound mapped:", exc)
    try:
        direct_mapped()
    except OSError as exc:
        print("unmapped errno stays plain:", exc)
    try:
        posthoc_plain()
    except FileNotFoundError:
        print("WRONG: post-hoc assignment re-mapped")
    except OSError as exc:
        print("post-hoc stays plain:", exc, exc.errno)
    try:
        raise FileNotFoundError(1, "Operation not permitted")
    except PermissionError:
        print("WRONG: explicit subclass re-mapped")
    except FileNotFoundError as exc:
        print("subclass kept:", exc)
    # The filename-carrying ctor maps too, and the filename travels.
    try:
        raise OSError(2, "No such file or directory", "cfg.txt")
    except FileNotFoundError as exc:
        print("filename mapped:", exc, exc.filename)
    # Zero-arg raise (kind none) stays a plain OSError.
    try:
        raise OSError()
    except OSError as exc:
        print("zero-arg plain:", str(exc) == "")


main()
