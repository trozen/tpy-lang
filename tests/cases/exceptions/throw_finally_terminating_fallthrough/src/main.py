# A try/except/finally whose finally always raises/returns must still run it
# when the try body falls through -- eliding that copy needs the try body AND
# every handler to terminate on their own.
from tpy import int32


def raise_from_finally() -> None:
    """Try body falls through; the finally's raise must still fire."""
    try:
        print("try body ran")
    except ValueError:
        print("unreachable handler")
    finally:
        print("finally ran")
        raise RuntimeError("from finally")


def return_from_finally() -> int32:
    """The finally's return is the function's only exit on this path."""
    try:
        print("try body ran")
    except ValueError:
        print("unreachable handler")
    finally:
        print("finally ran")
        return 7


def handler_falls_through() -> None:
    """Try terminates but the handler does not -- finally still needed."""
    try:
        raise ValueError("boom")
    except ValueError:
        print("handler ran")
    finally:
        print("finally ran")
        raise RuntimeError("from finally")


def finally_return_wins() -> int32:
    """Handler returns, then the finally's return overrides it (Python)."""
    try:
        raise ValueError("boom")
    except ValueError:
        return 1
    finally:
        return 2


def tuple_clause_falls_through() -> None:
    """A tuple clause expands to one handler per element, all sharing a body.

    The fall-through decision asks every handler, so the expansion must not
    change the answer the single written clause implies.
    """
    try:
        raise ValueError("boom")
    except (ValueError, TypeError):
        print("handler ran")
    finally:
        print("finally ran")
        raise RuntimeError("from finally")


def deep_terminating_finally(c: bool) -> None:
    """The finally terminates via both if-branches, not a literal last raise.

    Exercises the recursive termination check: the fall-through copy is still
    required even though the finally's last statement is not itself a raise.
    """
    try:
        print("try body ran")
    except ValueError:
        print("unreachable handler")
    finally:
        if c:
            raise RuntimeError("from if-branch")
        else:
            raise RuntimeError("from else-branch")


def all_paths_terminate() -> int32:
    """Control: try and handler both terminate, so no fall-through exists.

    The finally is reached only via the return sites and the exception path;
    the normal-path copy is correctly elided here.
    """
    try:
        return 10
    except ValueError:
        return 20
    finally:
        print("finally ran")


def main() -> None:
    try:
        raise_from_finally()
    except RuntimeError:
        print("caught from raise_from_finally")

    print(return_from_finally())

    try:
        handler_falls_through()
    except RuntimeError:
        print("caught from handler_falls_through")

    print(finally_return_wins())

    try:
        tuple_clause_falls_through()
    except RuntimeError:
        print("caught from tuple_clause_falls_through")

    try:
        deep_terminating_finally(True)
    except RuntimeError:
        print("caught from deep if-branch")

    try:
        deep_terminating_finally(False)
    except RuntimeError:
        print("caught from deep else-branch")

    print(all_paths_terminate())


main()
