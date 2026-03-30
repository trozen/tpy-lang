# except Exception catches any derived exception type (C++ polymorphic catch)
def main() -> None:
    try:
        raise ValueError("test")
    except Exception:
        print("caught as Exception")

main()
