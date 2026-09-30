# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from irisecho_core import cli
from irisecho_core.platform_support import support_tier


@pytest.mark.parametrize(
    ("system", "machine", "tier"),
    [
        ("Windows", "AMD64", "supported"),
        ("Darwin", "arm64", "preview"),
        ("Darwin", "x86_64", "unsupported (Intel Mac)"),
        ("Linux", "x86_64", "works, unsupported"),
        ("FreeBSD", "amd64", "unsupported"),
    ],
)
def test_support_tier(system, machine, tier):
    assert support_tier(system, machine) == tier


def test_info_command(capsys):
    assert cli.main(["info"]) == 0
    out = capsys.readouterr().out
    assert "tier: " in out
