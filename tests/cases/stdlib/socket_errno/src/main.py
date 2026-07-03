# Structured OSError attributes (CPython parity): a refused connect carries
# `.errno` (== errno.ECONNREFUSED) and `.strerror`; a failed name resolution
# raises socket.gaierror (an OSError) with the EAI_* code in `.errno`. Raw
# errno / EAI values are host-divergent, so only comparisons are printed.
# str(e) formats differ between TPy and CPython, so assert containment only.
import errno
import socket
from socket import gaierror


def refused() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # A bound timeout guards against a host that DROPs (rather than REJECTs)
    # loopback; the refused connect returns ECONNREFUSED well before it.
    s.settimeout(5.0)
    try:
        s.connect(("127.0.0.1", 1))
        print("NO ERROR")
    except OSError as e:
        print("refused:", e.errno == errno.ECONNREFUSED, e.strerror in str(e))
    s.close()


def resolve_failure() -> None:
    # "bad name!" is not a legal hostname; getaddrinfo rejects it with
    # EAI_NONAME (no listener or network involved, deterministic).
    try:
        socket.gethostbyname("bad name!")
        print("NO ERROR")
    except gaierror as e:
        print("gaierror:", e.errno != 0, len(e.strerror) > 0,
              e.strerror in str(e))


def unset_defaults() -> None:
    # A hand-constructed OSError carries the unset defaults (0 / "" in TPy;
    # CPython has None there, so compare via truthiness-equivalent checks
    # that hold for both).
    e = OSError("plain")
    print("unset:", not e.errno, not e.strerror)


def main() -> None:
    refused()
    resolve_failure()
    unset_defaults()


main()
