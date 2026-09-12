# Cycle members can use `import as` aliases; the alias still finds
# the peer's pre-populated skeleton.
from a import A
from tpy import int32

def main() -> None:
    print(A().go())

main()
