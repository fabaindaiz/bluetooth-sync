import subprocess
import sys
from importlib.metadata import version

import pytest

from aurasync import __version__
from aurasync.cli import main


def test_version_matches_installed_metadata():
    assert version("aurasync") == __version__


def test_version_flag_prints_name_and_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"aurasync {__version__}"


def test_unknown_subcommand_fails_instead_of_doing_nothing():
    with pytest.raises(SystemExit) as exit_info:
        main(["play"])
    assert exit_info.value.code == 2


def test_module_entry_point_runs():
    result = subprocess.run(
        [sys.executable, "-m", "aurasync", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"aurasync {__version__}"
