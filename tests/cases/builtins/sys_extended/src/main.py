# Tests for sys.maxsize and sys.platform
import sys

def main() -> None:
    # maxsize is 2^63-1 on 64-bit platforms
    print(sys.maxsize > 0)
    print(sys.maxsize >= 2147483647)

    # platform is a non-empty string
    print(len(sys.platform) > 0)

main()
