# Sibling of the qualified-ctor shadow bug for module functions: `fa.make(...)`
# must call fa.make even when a same-named fb.make is bare-imported here.
import fa
from fb import make

def main() -> None:
    print(fa.make(5))   # 1005 -- qualified fa.make (int), with bare fb.make in scope
    print(make("xy"))   # 2 -- bare import is fb.make (str)

main()
