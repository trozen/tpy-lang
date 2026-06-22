# Regression guard for getsockopt_int: setting SO_REUSEADDR then reading it
# back returns a nonzero value (the option is enabled). Print the boolean,
# not the raw int -- the kernel may report 1 or a nonzero flag word, and
# that varies by platform, so only the enabled/disabled fact is portable.
from socket import socket, AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR


def main() -> None:
    s = socket(AF_INET, SOCK_STREAM)
    print(s.getsockopt_int(SOL_SOCKET, SO_REUSEADDR) != 0)
    s.setsockopt_int(SOL_SOCKET, SO_REUSEADDR, 1)
    print(s.getsockopt_int(SOL_SOCKET, SO_REUSEADDR) != 0)


main()
