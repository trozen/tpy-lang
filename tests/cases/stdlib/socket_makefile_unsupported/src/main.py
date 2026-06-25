# makefile v1 implements binary read mode only; text (incl. the bare
# makefile() default), write, and unbuffered (buffering=0) modes raise
# ValueError. no_cpython -- CPython returns text/writer/raw objects for these
# instead of raising; this pins the documented v1 limitation.
import socket


def main() -> None:
    a, b = socket.socketpair()
    try:
        b.makefile()          # default mode "r" is text -> unsupported
        print("bare no-raise")
    except ValueError:
        print("bare ValueError")
    try:
        b.makefile("r")       # explicit text
        print("text no-raise")
    except ValueError:
        print("text ValueError")
    try:
        b.makefile("wb")      # write
        print("write no-raise")
    except ValueError:
        print("write ValueError")
    try:
        b.makefile("rb", 0)   # unbuffered (CPython returns raw SocketIO)
        print("unbuffered no-raise")
    except ValueError:
        print("unbuffered ValueError")
    a.close()
    b.close()


main()
