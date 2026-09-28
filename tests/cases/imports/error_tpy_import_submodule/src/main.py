# `from tpy import bits` names a real submodule, but the from-import form does
# not bind submodules yet (BUGS.md#tpy-from-import-submodule); the import is
# rejected with the forms that work.
from tpy import bits  # tpyc: error(/'from tpy import bits' does not bind the submodule yet; use 'import tpy.bits'/)
from tpy import uint32


def main() -> None:
    x: uint32 = 12
    print(bits.rotl32(x, 1))


main()
