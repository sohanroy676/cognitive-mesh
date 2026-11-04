"""Does a version fit a declared range (ADR-0041)? Unclear specs are unknown, never a pass."""

import pytest

from cognitive_mesh.impact.versions import Fit, spec_of, version_fits


@pytest.mark.parametrize(
    ("spec", "version", "fit"),
    [
        (">=1.0,<2", "1.9.0", Fit.INSIDE),
        (">=1.0,<2", "2.0.0", Fit.OUTSIDE),
        ("==1.2.*", "1.3.0", Fit.OUTSIDE),
        ("~=1.4", "1.9", Fit.INSIDE),
        ("", "9.9", Fit.INSIDE),
        (">=1.0", "not-a-version", Fit.UNKNOWN),
        ("???", "1.0", Fit.UNKNOWN),
    ],
)
def test_pep440(spec: str, version: str, fit: Fit) -> None:
    assert version_fits("pypi", spec, version) is fit


@pytest.mark.parametrize(
    ("spec", "version", "fit"),
    [
        ("^1.2.3", "1.9.0", Fit.INSIDE),
        ("^1.2.3", "2.0.0", Fit.OUTSIDE),
        ("^0.2.3", "0.3.0", Fit.OUTSIDE),
        ("^0.0.3", "0.0.4", Fit.OUTSIDE),
        ("~1.2.3", "1.2.9", Fit.INSIDE),
        ("~1.2.3", "1.3.0", Fit.OUTSIDE),
        ("~1", "1.9.0", Fit.INSIDE),
        (">=1.0.0 <2.0.0", "1.5.0", Fit.INSIDE),
        (">= 1.0.0 < 2.0.0", "2.5.0", Fit.OUTSIDE),
        ("1.2.x", "1.2.9", Fit.INSIDE),
        ("1.2", "1.3.0", Fit.OUTSIDE),
        ("*", "7.0.0", Fit.INSIDE),
        ("1.2.3", "1.2.3", Fit.INSIDE),
        ("^1 || ^2", "2.0.0", Fit.UNKNOWN),
        ("1.0.0 - 2.0.0", "1.5.0", Fit.UNKNOWN),
        ("latest", "1.0.0", Fit.UNKNOWN),
        (">1.2", "1.5.0", Fit.UNKNOWN),
        ("^1.0.0", "2.0.0-beta.1", Fit.UNKNOWN),
        ("^1.0.0", "2.x", Fit.UNKNOWN),
        ("github:user/repo", "1.0.0", Fit.UNKNOWN),
    ],
)
def test_npm_subset(spec: str, version: str, fit: Fit) -> None:
    assert version_fits("npm", spec, version) is fit


def test_spec_of_reads_the_manifest_detail_of_each_ecosystem() -> None:
    assert spec_of("pypi", "apilib>=1.0,<2 [runtime]") == "<2,>=1.0"
    assert spec_of("pypi", "apilib[extra]==1.2 ; python_version>'3' [dev]") == "==1.2"
    assert spec_of("pypi", "-e git+https://example.invalid/x") is None
    assert spec_of("npm", 'left-pad: "^1.2.0" [dependencies]') == "^1.2.0"
    assert spec_of("npm", '@scope/pkg: "~2" [devDependencies]') == "~2"
    assert spec_of("npm", "left-pad: {not json [dependencies]") is None
    assert spec_of("npm", "left-pad: 5 [dependencies]") is None
