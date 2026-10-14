# Test platform-filtered include directive
# The unconditional include should always be emitted,
# the windows-only include should be skipped on non-windows
__tpy__ = {"includes": ["native_types.hpp",
                        {"header": "windows_only.hpp", "platform": "win32"}]}
from tpy.extern import native
from tpy import int32

@native
def platform_value() -> int32: ...

def main() -> None:
    print(platform_value())

main()
