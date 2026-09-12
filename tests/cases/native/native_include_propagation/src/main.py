# Test that # tpy: include() from native modules is propagated to importers
from tpy import int32
from nativelib.ops import native_add

def main() -> None:
    print(native_add(int32(10), int32(32)))

main()
