# subprocess.Popen takes an argument list only: a str command (CPython's
# shell-free single-program spelling) is a compile error, not a runtime one.
from subprocess import Popen


def main() -> None:
    p = Popen("ls")  # tpyc: error(/Type mismatch in argument 'args': expected list\[str\], got str/)
    print(p.wait())


main()
