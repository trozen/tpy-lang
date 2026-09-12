from tpy import int32
# Import submodule - should execute parent __init__ first
from mypackage.utils import helper

def main() -> int32:
    helper()
    return int32(0)

main()
