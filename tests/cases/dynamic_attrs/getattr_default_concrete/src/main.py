# D16 v1.5 phase 8: 3-arg getattr on a homogeneous dyn-readable class.
# Returns the dunder's value on success, the default on AttributeError.

class Headers:
    def __getattr__(self, name: str) -> str:
        if name == "host":
            return "example.com"
        raise AttributeError(name)


def main() -> None:
    h = Headers()
    print(getattr(h, "host", "fallback"))      # dunder -> "example.com"
    print(getattr(h, "absent", "fallback"))    # dunder raises -> "fallback"


main()
