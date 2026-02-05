from tpy import Int32
# Import submodule - should execute parent __init__ first
from mypackage.utils import helper

def main() -> Int32:
    helper()
    return Int32(0)

main()
