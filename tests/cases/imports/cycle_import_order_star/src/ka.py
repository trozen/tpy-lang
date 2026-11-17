# K is bound by the first star import, before `ka` starts `kb`; the later
# explicit import of the same name does not delay it.
from kc import *  # noqa: F403


def main() -> None:
    k = K(1)
    kb.bump(k)
    # `bump` took the object main holds by reference.
    print("first binding:", k.v)


import kb  # noqa: E402
from kc import K  # noqa: E402
