# A regular module whose only cycle peer is native is no C++ cycle member,
# but the executable-top-level-code rule still holds over the import cycle.
from m import COUNT

def main() -> None:
    print(COUNT)

main()
