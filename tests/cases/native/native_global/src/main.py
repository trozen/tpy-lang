from tpy.extern import native_global
from tpy import Int32

# C++ global import with namespace-qualified name
score: Int32 = native_global("engine::score")

# C++ global import without rename
lives: Int32 = native_global()

def main() -> None:
    print(score)
    print(lives)

main()
