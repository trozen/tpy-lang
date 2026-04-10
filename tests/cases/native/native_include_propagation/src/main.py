# Test that # tpy: include() from native modules is propagated to importers
from tpy import Int32
from nativelib.ops import native_add

def main() -> None:
    print(native_add(Int32(10), Int32(32)))

main()
