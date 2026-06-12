# Variables first bound inside a finally body: usable within the finally
# (despite the duplicated catch/normal-path emissions) and visible after
# the try statement, per Python function scoping. Covers the finally-only
# tier and the throw tier (try/except/finally).
def finally_only() -> None:
    try:
        print("body")
    finally:
        tmp = "x" + "y"
        print(tmp)
    print(tmp)


def throw_tier(trigger: bool) -> None:
    try:
        if trigger:
            raise ValueError("boom")
        print("ok")
    except ValueError:
        print("caught")
    finally:
        note = "done" + ("!" if trigger else ".")
        print(note)
    print(note)


def main() -> None:
    finally_only()
    throw_tier(False)
    throw_tier(True)


main()
