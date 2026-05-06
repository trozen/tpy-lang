# Cyclic import where a peer imports an @overload-grouped function
# defined in another peer. Both overloads must be reachable through
# the import.
from a import use_g_int, use_g_str
from b import relay

def main() -> None:
    print(use_g_int(7))
    print(use_g_str("hello"))
    print(relay(3))

main()
