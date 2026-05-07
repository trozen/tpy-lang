# MemoryError -- catchable. The runtime currently leaves OOM as a
# panic (catching MemoryError is fragile because the handler may itself
# allocate); the class is exposed so user code can `raise MemoryError(...)`
# for higher-level allocation-failure paths it controls.


def fail() -> None:
    raise MemoryError("custom: cannot allocate")


def main() -> None:
    try:
        fail()
    except MemoryError as e:
        print("caught MemoryError:", str(e))


main()
