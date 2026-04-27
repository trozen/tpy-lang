# print(..., file=sys.stdout/sys.stderr) routes through StdStream wrappers.
# stderr output is not captured by the test runner; we just verify it doesn't
# crash and that explicit sys.stdout matches the default.
import sys

def main() -> None:
    print("default sink")
    print("explicit stdout", file=sys.stdout)
    print("to stderr", file=sys.stderr)
    print("after stderr write")

main()
