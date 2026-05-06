# Cycle members may not include re-export facades like
# `pkg/__init__.py`. The reject gate fires when the package init
# is one of the cycle members.
from pkg import Boosted
from tpy import Int32

def main() -> Int32:
    return Boosted(3).boost()

main()
