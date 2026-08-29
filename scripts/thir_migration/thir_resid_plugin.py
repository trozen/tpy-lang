"""Pytest plugin to partition test_case items by no_thir.txt presence.

Controlled by env RESID_ONLY:
  unmarked -> keep only cases WITHOUT no_thir.txt (ratchet-clean, zero fallback)
  marked   -> keep only cases WITH no_thir.txt
  all/unset -> no filtering

Used to build the A-minus-U residency metric for the THIR migration. With
`no_thir.txt` markers at zero the `marked` selection is empty and `unmarked`
is the whole corpus, so the partition no longer separates anything -- see the
note in thir_ast_arm_residency.py.
"""
import os


def pytest_collection_modifyitems(config, items):
    mode = os.environ.get("RESID_ONLY", "all")
    if mode == "all":
        return
    keep = []
    deselected = []
    for item in items:
        params = getattr(getattr(item, "callspec", None), "params", {})
        case_dir = params.get("case_dir")
        if case_dir is None:
            keep.append(item)  # non-parametrized / other test modules
            continue
        marked = (case_dir / "no_thir.txt").exists()
        want = (mode == "marked") == marked
        if want:
            keep.append(item)
        else:
            deselected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = keep
