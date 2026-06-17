# os.path -- common pathname manipulations (POSIX semantics).
# tpy: cpp_namespace("tpystd::os::path")
#
# v1 is the pure-string surface of CPython's posixpath: no filesystem
# access, no cwd/environ. The predicate tier (exists/isfile/isdir/...) and
# the cwd/env-dependent functions (abspath/realpath/expanduser/...) are
# deferred to the os filesystem-bindings effort -- see TODO.md.
from typing import Final

sep: Final[str] = "/"
pathsep: Final[str] = ":"
extsep: Final[str] = "."
curdir: Final[str] = "."
pardir: Final[str] = ".."
defpath: Final[str] = "/bin:/usr/bin"
devnull: Final[str] = "/dev/null"


def isabs(p: str) -> bool:
    return p.startswith("/")


def basename(p: str) -> str:
    i = p.rfind("/") + 1
    return p[i:]


def dirname(p: str) -> str:
    i = p.rfind("/") + 1
    head = p[:i]
    # posixpath keeps a head that is entirely slashes ("/" or "//") intact.
    j = len(head)
    while j > 0 and head[j - 1] == "/":
        j -= 1
    if j > 0:
        head = head[:j]
    return head


def split(p: str) -> tuple[str, str]:
    return (dirname(p), basename(p))


def splitext(p: str) -> tuple[str, str]:
    sep_index = p.rfind("/")
    dot_index = p.rfind(".")
    if dot_index > sep_index:
        # Skip leading dots in the basename: ".bashrc" has no extension.
        fname_index = sep_index + 1
        while fname_index < dot_index:
            if p[fname_index] != ".":
                return (p[:dot_index], p[dot_index:])
            fname_index += 1
    return (p, "")


def splitdrive(p: str) -> tuple[str, str]:
    return ("", p)


def join(a: str, *paths: str) -> str:
    path = a
    for b in paths:
        if b.startswith("/"):
            path = b
        elif len(path) == 0 or path.endswith("/"):
            path = path + b
        else:
            path = path + "/" + b
    return path


def normpath(p: str) -> str:
    if len(p) == 0:
        return "."
    # POSIX: exactly two leading slashes are kept; one or three-plus collapse.
    initial_slashes = 0
    if p.startswith("/"):
        initial_slashes = 1
        if p.startswith("//") and not p.startswith("///"):
            initial_slashes = 2
    new_comps: list[str] = []
    for comp in p.split("/"):
        if comp == "" or comp == ".":
            continue
        if comp != ".." or (initial_slashes == 0 and len(new_comps) == 0) or (
                len(new_comps) > 0 and new_comps[-1] == ".."):
            new_comps.append(comp)
        elif len(new_comps) > 0:
            new_comps.pop()
    path = "/".join(new_comps)
    if initial_slashes > 0:
        path = "/" * initial_slashes + path
    if len(path) == 0:
        return "."
    return path


def commonprefix(m: list[str]) -> str:
    # Character-level common prefix, not path-aware (matches CPython).
    if len(m) == 0:
        return ""
    first = m[0]
    prefix_len = len(first)
    for s in m:
        if len(s) < prefix_len:
            prefix_len = len(s)
    i = 0
    while i < prefix_len:
        c = first[i]
        for s in m:
            if s[i] != c:
                return first[:i]
        i += 1
    return first[:prefix_len]
