# Dependency provenance / 依赖来源

No runtime framework was added. Runtime uses Python's standard library plus the original psutil and pyserial dependencies. Exact release versions, official source commits, licenses, installed wheel URLs, SHA-256 and installation time are recorded in [dependency-audit.json](dependency-audit.json).

| Package | Version | License | Role |
| --- | --- | --- | --- |
| psutil | 7.2.2 | BSD-3-Clause | Interface/system inspection |
| pyserial | 3.5 | BSD-3-Clause | Serial transport |
| pytest | 8.4.2 | MIT | Test runner |
| coverage | 7.10.6 | Apache-2.0 | Scoped coverage |
| iniconfig | 2.1.0 | MIT | Pytest dependency |
| packaging | 25.0 | Apache-2.0 / BSD-2-Clause | Pytest dependency |
| pluggy | 1.6.0 | MIT | Pytest dependency |
| Pygments | 2.19.2 | BSD-2-Clause | Pytest dependency |
| colorama | 0.4.6 | BSD-3-Clause | Windows test output |
| exceptiongroup | 1.3.1 | MIT / PSF-2.0 | Pytest on Python 3.10 |
| tomli | 2.3.0 | MIT | Pytest on Python 3.10 |
| typing_extensions | 4.15.0 | PSF-2.0 | exceptiongroup on Python 3.10 |

`requirements-runtime.lock` contains the two runtime releases. `requirements.lock` contains the complete test environment. Both use release artifact hashes; run `python -m pip install --require-hashes -r <lockfile>`. The corresponding `.txt` files are readable version manifests. Source tag commits were checked against official repositories; installed artifacts are PyPI distributions, not source builds. The audit records their separate identities without claiming bit-for-bit reproducibility between wheel and source commit. Hash verification verifies the selected distribution, not absence of vulnerabilities.

The three Python 3.10 compatibility packages are conditional test dependencies (`python_version < "3.11"`). Their audited source commits and release hashes are recorded separately from the local Python 3.13 installation; their actual installation is recorded in the Python 3.10 CI logs. These packages are excluded from runtime bundles.

CI Actions are fixed to reviewed commits: `actions/checkout` v4.2.2 at `11bd71901bbe5b1630ceea73d27597364c9af683`; `actions/setup-python` v5.6.0 at `a26af69be951a213d495a4c3e4e4022e16d87065` (both MIT). Runner images and Python patch versions remain platform-managed and are not fully reproducible artifacts. No new third-party Skill was installed or cloned.

The optional browser regression uses an existing local Playwright/Chrome installation. It is not part of the application runtime or hash-locked Python environment. See [validation](VALIDATION.md) for the versions actually exercised.

Release bundles include only the reviewed runtime wheels, selected for Windows x64 or Linux x86_64. `tools/build_release.py` refuses artifacts outside the audited SHA-256 set and includes source provenance plus copies of wheel license files. Python itself remains a prerequisite. Bundle installation uses `--no-index --find-links wheels`, following [pip's official offline download/install workflow](https://pip.pypa.io/en/stable/cli/pip_download/).
