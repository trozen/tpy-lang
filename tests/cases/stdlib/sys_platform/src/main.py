# sys.platform, platform.system() / machine(), sys.byteorder and sys.maxsize
# hold the BUILD TARGET's values, read at every position. Compares only, so
# the output is the same on every supported target.
import asyncio
import platform
import sys
import reexport
from typing import Iterator
from sys import platform as plat


def describe() -> str:
    # function: compare and startswith on sys.platform
    if sys.platform == "darwin" or sys.platform.startswith("freebsd"):  # tpyc: ok
        return "bsd-ish"
    return sys.platform


class Probe:
    def posix(self) -> bool:
        # method
        return sys.platform != "win32"  # tpyc: ok


def gen() -> Iterator[bool]:
    # generator
    yield sys.platform == plat  # tpyc: ok


async def coro() -> bool:
    # async
    await asyncio.sleep(0)
    return sys.platform == "linux" or sys.platform == "darwin"  # tpyc: ok


def system_matches() -> bool:
    # platform.system() names the OS sys.platform names
    name = platform.system()
    if sys.platform == "linux":
        return name == "Linux"
    if sys.platform == "darwin":
        return name == "Darwin"
    if sys.platform.startswith("freebsd"):
        return name == "FreeBSD"
    return name == "Windows"


def machine_matches() -> bool:
    # platform.machine() is the target's architecture, spelled as on its OS
    m = platform.machine()  # tpyc: ok
    if m not in ("x86_64", "aarch64", "arm64", "amd64", "AMD64", "ARM64"):
        return False
    if sys.platform == "linux":
        return m in ("x86_64", "aarch64")
    if sys.platform == "darwin":
        return m in ("x86_64", "arm64")
    if sys.platform.startswith("freebsd"):
        return m in ("amd64", "arm64")
    return m in ("AMD64", "ARM64")


def main() -> None:
    print("function:", describe() in ("linux", "bsd-ish"))
    print("method:", Probe().posix())

    def check() -> bool:
        # closure
        return sys.platform == plat  # tpyc: ok
    print("closure:", check())
    for same in gen():
        print("generator:", same)
    print("async:", asyncio.run(coro()))
    xs = [1, 2]
    # comprehension filter
    print("comprehension:", [x for x in xs if sys.platform != ""])  # tpyc: ok
    # `from sys import platform` binds the same value
    print("from-import:", plat == sys.platform, plat.startswith("lin") == sys.platform.startswith("lin"))
    print("system:", system_matches())
    print("machine:", machine_matches())
    print("byteorder:", sys.byteorder in ("little", "big"))  # tpyc: ok
    # every supported target is 64-bit
    print("maxsize:", sys.maxsize == 2**63 - 1)  # tpyc: ok
    # module attribute of a module re-exporting sys's constants
    print("reexport:", reexport.platform == sys.platform, reexport.maxsize == sys.maxsize)  # tpyc: ok


# module level
print("module:", sys.platform != "" and plat == sys.platform)  # tpyc: ok
main()
