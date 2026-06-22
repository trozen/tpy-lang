# os.path -- common pathname manipulations (POSIX semantics).
# tpy: cpp_namespace("tpystd::os::path")
# tpy: include("<tpy/stdlib/os.hpp>")
from typing import Final
from tpy import Char, Own
from ._native import (
    getcwd, exists, lexists, isfile, isdir, islink, getsize, realpath,
    path_getmtime as getmtime, path_getatime as getatime,
    path_getctime as getctime, path_samefile as samefile,
    path_ismount as ismount,
    current_home as _current_home, user_home as _user_home,
)
from . import _environ
# samestat takes stat_result; import it from the type-only `_types` leaf rather
# than the parent `os` module (importing os here would be an executable-bearing
# cyclic import, which TPy rejects).
from ._types import stat_result

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


def _join2(a: str, b: str) -> str:
    if b.startswith("/"):
        return b
    if len(a) == 0 or a.endswith("/"):
        return a + b
    return a + "/" + b


def join(a: str, *paths: str) -> str:
    path = a
    for b in paths:
        path = _join2(path, b)
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


# Longest common sub-path of a list of paths, component-wise (unlike the
# character-level commonprefix). Empty list or a mix of absolute and relative
# paths raises ValueError, matching CPython.
def commonpath(paths: list[str]) -> str:
    if len(paths) == 0:
        raise ValueError("commonpath() arg is an empty sequence")
    has_abs = False
    has_rel = False
    for p in paths:
        if p.startswith("/"):
            has_abs = True
        else:
            has_rel = True
    if has_abs and has_rel:
        raise ValueError("Can't mix absolute and relative paths")
    split_paths: list[list[str]] = []
    for p in paths:
        comps: list[str] = []
        for c in p.split("/"):
            if len(c) > 0 and c != ".":
                comps.append(c)
        split_paths.append(comps)
    common: list[str] = []
    first = split_paths[0]
    i = 0
    while i < len(first):
        comp = first[i]
        ok = True
        for sp in split_paths:
            if i >= len(sp) or sp[i] != comp:
                ok = False
        if not ok:
            break
        common.append(comp)
        i += 1
    prefix = "/" if has_abs else ""
    return prefix + "/".join(common)


# POSIX: case-preserving filesystem, so normcase is the identity (it only
# folds case / normalizes separators on Windows).
def normcase(p: str) -> str:
    return p


def samestat(s1: stat_result, s2: stat_result) -> bool:
    return s1.st_ino == s2.st_ino and s1.st_dev == s2.st_dev


def abspath(p: str) -> str:
    if isabs(p):
        return normpath(p)
    return normpath(_join2(getcwd(), p))


def _split_parts(s: str) -> Own[list[str]]:
    out: list[str] = []
    for c in s.split("/"):
        if len(c) > 0:
            out.append(c)
    return out


# Relative path from `start` to `path` (posixpath.relpath). Both are made
# absolute first, so the current directory cancels in the common prefix.
def relpath(p: str, start: str = ".") -> str:
    start_parts = _split_parts(abspath(start))
    path_parts = _split_parts(abspath(p))
    i = 0
    common = min(len(start_parts), len(path_parts))
    while i < common and start_parts[i] == path_parts[i]:
        i += 1
    rel: list[str] = []
    j = i
    while j < len(start_parts):
        rel.append("..")
        j += 1
    while i < len(path_parts):
        rel.append(path_parts[i])
        i += 1
    if len(rel) == 0:
        return "."
    return "/".join(rel)


def _is_var_char(c: Char) -> bool:
    return (c >= "a" and c <= "z") or (c >= "A" and c <= "Z") or (
        c >= "0" and c <= "9") or c == "_"


# Expand $name and ${name} from the environment (posixpath.expandvars). An
# unset name -- or a `$` not starting a valid reference -- is left verbatim.
def expandvars(p: str) -> str:
    if p.find("$") < 0:
        return p
    res = ""
    i = 0
    n = len(p)
    while i < n:
        if p[i] != "$":
            start = i
            while i < n and p[i] != "$":
                i += 1
            res = res + p[start:i]
        elif i + 1 < n and p[i + 1] == "{":
            close = i + 2
            while close < n and p[close] != "}":
                close += 1
            if close >= n:
                res = res + p[i:]
                i = n
            else:
                name = p[i + 2:close]
                if name in _environ.environ:
                    res = res + _environ.environ[name]
                else:
                    res = res + p[i:close + 1]   # unset -> leave verbatim
                i = close + 1
        else:
            end = i + 1
            while end < n and _is_var_char(p[end]):
                end += 1
            if end == i + 1:
                res = res + "$"
                i += 1
            else:
                name = p[i + 1:end]
                if name in _environ.environ:
                    res = res + _environ.environ[name]
                else:
                    res = res + p[i:end]
                i = end
    return res


def _rstrip_slashes(s: str) -> str:
    j = len(s)
    while j > 0 and s[j - 1] == "/":
        j -= 1
    return s[:j]


# Expand a leading ~ / ~user (posixpath.expanduser). ~ uses $HOME (from the
# os.environ snapshot), falling back to the current user's pwd home; ~user uses
# pwd. An unresolvable ~ / unknown ~user is left verbatim.
def expanduser(p: str) -> str:
    if not p.startswith("~"):
        return p
    # First slash at or after index 1 (str.find has no start arg here).
    rel = p[1:].find("/")
    i = len(p) if rel < 0 else rel + 1
    userhome = ""
    if i == 1:
        if "HOME" in _environ.environ:
            userhome = _environ.environ["HOME"]
        else:
            userhome = _current_home()
            if len(userhome) == 0:
                return p
    else:
        userhome = _user_home(p[1:i])
        if len(userhome) == 0:
            return p
    result = _rstrip_slashes(userhome) + p[i:]
    if len(result) == 0:
        return "/"
    return result
