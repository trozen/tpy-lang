# A native module is declaration-only at the statement level too: any
# module-level statement but an import, a bare declaration or a
# native_global binding is rejected.
from nm import f

def main() -> None:
    print(f(1))

main()
