# MIR over calls reaching declared OS / clock / bit stubs (`transient=True`, `@pure`): a str
# parameter published as a view is read, a void result lends nothing, an undeclared result is fresh.
import os
import time
from tpy import StrView, int32, uint32
from tpy.bits import rotl32
from tpy.extern import cpp_template


class Holder:
    v: StrView

    def __init__(self, v: StrView):
        self.v = v


def set_v(h: Holder, v: str) -> None:
    h.v = v


# transient stub with a str parameter: the path is published as a view, so the stub only reads it
def probe_exists(name: str) -> bool:  # tpyc: mir(covered)
    return os.path.exists(name)  # tpyc: ok


# void stub: os.rmdir returns nothing, so no result borrows the path
def drop_dir(name: str) -> None:  # tpyc: mir(covered)
    os.rmdir(name)  # tpyc: ok


# transient clock: time.monotonic takes no argument and returns a fresh float
def elapsed_nonneg() -> bool:  # tpyc: mir(covered)
    t0 = time.monotonic()  # tpyc: ok
    return time.monotonic() >= t0


# @pure scalar stub: rotl32 reads its two scalars
def rotate(x: uint32, n: int32) -> uint32:  # tpyc: mir(covered)
    return rotl32(x, n)  # tpyc: ok


# view of a temporary: the getcwd result dies with its statement while h.v still views it (never run)
def cwd_dangle() -> None:
    h = Holder("init")
    set_v(h, os.getcwd())  # tpyc: warning(/the temporary 'os.getcwd\(\)' lives only until the end of this statement, and 'h.v' may still view it and is read on line /)
    print(h.v)


# view of a temporary, safe twin: the result is bound to a local that outlives the read
def cwd_bound() -> bool:
    h = Holder("init")
    cwd = os.getcwd()
    set_v(h, cwd)  # tpyc: ok
    return h.v == cwd


# check point: time.sleep delivers signals inside the sleep, so it declares no contract
def nap() -> None:  # tpyc: mir(uncovered /stub declares no contract/)
    time.sleep(0.0)


# str -> str stub: os.readlink is transient and its undeclared result is fresh
def link_target(name: str) -> str:  # tpyc: mir(covered)
    return os.readlink(name)


# user transient stub, str -> str: declaring no origin, its result is fresh
@cpp_template("std::string({0})", transient=True)
def duplicate(s: str) -> str: ...


def grow(names: list[str], s: str) -> int32:
    names.append("second")
    return len(s)


# a fresh result read while `grow` appends to `names`: no alias warning
def fresh_safe() -> int32:  # tpyc: mir(covered)
    names = ["first"]
    return grow(names, duplicate(names[0]))  # tpyc: ok


# a fresh temporary viewed by `h.v` past its statement: warned (never run)
def fresh_dangling(s: str) -> None:
    h = Holder("initial")
    set_v(h, duplicate(s))  # tpyc: warning(/the temporary 'duplicate\(s\)' lives only until the end of this statement, and 'h.v' may still view it/)
    print(h.v)


def main() -> None:
    os.mkdir("stubc_dir")
    print("exists_before", probe_exists("stubc_dir"))
    drop_dir("stubc_dir")
    print("exists_after", probe_exists("stubc_dir"))
    print("elapsed", elapsed_nonneg())
    print("rotate", rotate(1, 31))
    print("cwd_bound", cwd_bound())
    nap()
    os.symlink("target_name", "stubc_link")
    print("link_target", link_target("stubc_link"))
    os.unlink("stubc_link")

