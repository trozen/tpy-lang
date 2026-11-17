# B is bound by the star import from the finished `rt`, before `xa` starts
# `xb`; Late only by the star import after it, and y never: it is only declared.
from rt import *  # noqa: F403
import xb
from xd import *  # noqa: F403
y: int


def main() -> None:
    print("bindings:", xb.read())
