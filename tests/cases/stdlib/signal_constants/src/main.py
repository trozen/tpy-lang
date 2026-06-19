# signal.SIGINT / SIGTERM are sourced from <signal.h> via native_global so the
# generated constant doesn't collide with the libc SIGINT/SIGTERM macros, which
# are in scope in every generated TU on macOS (a plain constexpr named SIGINT
# expands to `int32_t 2 = 2`). Regression guard for that collision.
from signal import SIGINT, SIGTERM


def main():
    print(int(SIGINT))
    print(int(SIGTERM))


main()
