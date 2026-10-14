# A platform-filtered include names the build target: the win32 include is
# rendered under `#if defined(_WIN32)`, so the target compiler's preprocessor
# decides it, and the unconditional include is emitted as is.
__tpy__ = {"includes": ["native_types.hpp",
                        # guarded by the target's macro, never dropped at codegen
                        {"header": "windows_only.hpp", "platform": "win32"}]}
from tpy.extern import native
from tpy import int32

@native
def platform_value() -> int32: ...

def main() -> None:
    print(platform_value())

main()
