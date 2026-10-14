# Two regular modules in an import cycle through a native module stay C++
# cycle peers: a by-value field of the peer's record is still rejected, and
# the diagnostic names the whole import cycle, the native module included.
from a import A

def main() -> None:
    pass

main()
