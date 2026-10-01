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


# -- aurasync panel ---------------------------------------------------------------

from aurasync.cli import PanelConfigError, panel_config  # noqa: E402


def test_panel_refuses_to_start_without_the_demo_engine(capsys):
    assert main(["panel"]) == 2
    assert "bloqueado" in capsys.readouterr().err


def test_panel_listens_only_on_localhost_by_default():
    config = panel_config(port=8737, lan=False, lan_address=lambda: "192.168.1.20", token="t")
    assert config.bind == "127.0.0.1"
    assert config.allowed_hosts == frozenset({"127.0.0.1", "localhost"})
    assert config.pairing_url is None
    assert config.local_url == "http://127.0.0.1:8737/?t=t"


def test_panel_on_the_lan_adds_the_lan_host_and_a_pairing_url():
    config = panel_config(port=8737, lan=True, lan_address=lambda: "192.168.1.20", token="t")
    assert config.bind == "0.0.0.0"
    assert "192.168.1.20" in config.allowed_hosts
    assert config.pairing_url == "http://192.168.1.20:8737/?t=t"


def test_panel_on_the_lan_without_a_lan_address_refuses():
    with pytest.raises(PanelConfigError, match="red local"):
        panel_config(port=8737, lan=True, lan_address=lambda: None, token="t")
