from tpy import native_global, Int32

# C++ global import with namespace-qualified name
score: Int32 = native_global("engine::score")

# C++ global import without rename
lives: Int32 = native_global()

def main() -> None:
    print(score)
    print(lives)

main()
