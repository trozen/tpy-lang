# os.path.expanduser: ~ / ~user expansion. HOME-based cases use a fixed HOME so
# output is machine-independent; the pwd-backed (~root) and HOME-unset fallback
# paths are checked by property (resolved + absolute) since the actual home is
# machine-specific. Byte-compared against CPython.
import os
from os.path import expanduser


def main():
    os.environ["HOME"] = "/home/tpytest"
    print(expanduser("~"))              # /home/tpytest
    print(expanduser("~/sub"))          # /home/tpytest/sub
    print(expanduser("~/"))             # /home/tpytest/
    print(expanduser("nochange/~"))     # verbatim -- ~ not leading
    print(expanduser("plain/path"))     # verbatim -- no ~
    print(expanduser("~nosuchuser_zzz"))    # verbatim -- unknown user
    print(expanduser("~nosuchuser_zzz/x"))  # verbatim -- unknown user

    # ~user via pwd: root exists on any unix; its home is absolute (exact value
    # is OS-specific -- /root on Linux, /var/root on macOS -- so check property).
    r = expanduser("~root")
    print(r != "~root" and r.startswith("/"))

    # HOME unset -> current user's pwd home (or verbatim "~" if no passwd entry);
    # either way non-empty. Machine-specific value, so check property.
    del os.environ["HOME"]
    h = expanduser("~")
    print(h == "~" or h.startswith("/"))


main()
