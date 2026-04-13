"""Unit tests for _parse_version_info in tpyc/__init__.py."""

import pytest

from tpyc import _parse_version_info


def test_final_release():
    assert _parse_version_info("0.1.0") == (0, 1, 0, "final", 0)
    assert _parse_version_info("1.2.3") == (1, 2, 3, "final", 0)
    assert _parse_version_info("12.34.56") == (12, 34, 56, "final", 0)


def test_alpha():
    assert _parse_version_info("0.1.0a1") == (0, 1, 0, "alpha", 1)
    assert _parse_version_info("2.0.0a12") == (2, 0, 0, "alpha", 12)


def test_beta():
    assert _parse_version_info("0.1.0b2") == (0, 1, 0, "beta", 2)


def test_release_candidate():
    assert _parse_version_info("0.1.0rc1") == (0, 1, 0, "candidate", 1)


def test_dev():
    assert _parse_version_info("0.1.0.dev0") == (0, 1, 0, "dev", 0)
    assert _parse_version_info("0.1.0.dev42") == (0, 1, 0, "dev", 42)


def test_invalid_raises():
    with pytest.raises(ValueError):
        _parse_version_info("")
    with pytest.raises(ValueError):
        _parse_version_info("1.2")                  # not enough components
    with pytest.raises(ValueError):
        _parse_version_info("1.2.3.4")              # too many components
    with pytest.raises(ValueError):
        _parse_version_info("1.2.3dev0")            # missing dot before dev
    with pytest.raises(ValueError):
        _parse_version_info("1.2.3.a1")             # spurious dot before alpha
    with pytest.raises(ValueError):
        _parse_version_info("1.2.3x1")              # unknown suffix
    with pytest.raises(ValueError):
        _parse_version_info("v1.2.3")               # leading junk


def test_unsupported_pep440_forms_raise():
    # Valid PEP 440 that tpyc doesn't ship today. If tpyc ever adopts one
    # of these forms, version_info needs a new releaselevel mapping and
    # this test should update accordingly.
    with pytest.raises(ValueError):
        _parse_version_info("1.2.3.post1")          # post-release
    with pytest.raises(ValueError):
        _parse_version_info("1!1.2.3")              # epoch
    with pytest.raises(ValueError):
        _parse_version_info("1.2.3+local")          # local identifier
