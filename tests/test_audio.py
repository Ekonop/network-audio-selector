from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from network_audio_selector.audio import SINK_NAME, AudioRouter


def test_audio_router_creates_virtual_sink(tmp_path):
    router = AudioRouter(state_dir=tmp_path)

    with patch.object(router, "_sink_names", return_value=["alsa_output.pci"]), \
         patch.object(router, "_default_sink", return_value="alsa_output.pci"), \
         patch("subprocess.run") as mock_run:

        mock_run.return_value = MagicMock(stdout="42\n")

        sink = router.create_virtual_sink()
        assert sink == SINK_NAME
        assert router.module_id == 42
        assert router.created_by_us is True
        assert router.previous_default_sink == "alsa_output.pci"

        # Check state file persisted
        state_file = tmp_path / "sink_state.json"
        assert state_file.exists()


def test_audio_router_adopts_existing_virtual_sink_safely(tmp_path):
    router = AudioRouter(state_dir=tmp_path)

    with patch.object(router, "_sink_names", return_value=["alsa_output.pci", SINK_NAME]), \
         patch.object(router, "_default_sink", return_value="alsa_output.pci"), \
         patch.object(router, "_existing_module_id", return_value=99), \
         patch("subprocess.run") as mock_run:

        sink = router.create_virtual_sink()
        assert sink == SINK_NAME
        assert router.module_id == 99
        assert router.created_by_us is False
        assert router.previous_default_sink == "alsa_output.pci"
        # Must NOT call pactl load-module
        for call in mock_run.call_args_list:
            assert "load-module" not in call[0][0]

        # Now test removal: must NOT unload module because created_by_us is False
        mock_run.reset_mock()
        with patch.object(router, "_sink_is_valid", return_value=True):
            router.remove_virtual_sink()

        for call in mock_run.call_args_list:
            assert "unload-module" not in call[0][0]
            if "set-default-sink" in call[0][0]:
                assert call[0][0][-1] == "alsa_output.pci"


def test_audio_router_creation_failure(tmp_path):
    router = AudioRouter(state_dir=tmp_path)

    with patch.object(router, "_sink_names", return_value=["alsa_output.pci"]), \
         patch.object(router, "_default_sink", return_value="alsa_output.pci"), \
         patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["pactl"])):

        with pytest.raises(subprocess.CalledProcessError):
            router.create_virtual_sink()

        assert router.created_by_us is False
        assert router.module_id is None


def test_audio_router_removes_and_restores_created_sink(tmp_path):
    router = AudioRouter(state_dir=tmp_path)
    router.module_id = 101
    router.created_by_us = True
    router.previous_default_sink = "headphones"

    def mock_run_impl(cmd, *args, **kwargs):
        if cmd == ["pactl", "list", "short", "modules"]:
            return MagicMock(stdout=f"101\tmodule-null-sink\tsink_name={SINK_NAME}\n")
        return MagicMock(stdout="")

    with patch.object(router, "_sink_is_valid", return_value=True), \
         patch("subprocess.run", side_effect=mock_run_impl) as mock_run:

        router.remove_virtual_sink()

        # Should unload module 101 and restore headphones
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert ["pactl", "unload-module", "101"] in calls
        assert ["pactl", "set-default-sink", "headphones"] in calls
        assert router.module_id is None
        assert not (tmp_path / "sink_state.json").exists()


def test_audio_router_restores_fallback_when_previous_sink_invalid(tmp_path):
    router = AudioRouter(state_dir=tmp_path)
    router.module_id = 102
    router.created_by_us = True
    router.previous_default_sink = "disconnected_usb_dac"

    def mock_run_impl(cmd, *args, **kwargs):
        if cmd == ["pactl", "list", "short", "modules"]:
            return MagicMock(stdout=f"102\tmodule-null-sink\tsink_name={SINK_NAME}\n")
        return MagicMock(stdout="")

    with patch.object(router, "_sink_is_valid", side_effect=lambda name: name == "fallback_speaker"), \
         patch.object(router, "_first_physical_sink", return_value="fallback_speaker"), \
         patch("subprocess.run", side_effect=mock_run_impl) as mock_run:

        router.remove_virtual_sink()
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert ["pactl", "set-default-sink", "fallback_speaker"] in calls


def test_verify_module_for_unload_validation(tmp_path):
    router = AudioRouter(state_dir=tmp_path)

    # 1. Matching module
    sample_output = (
        "1\tlibpipewire-module-rt\t\n"
        f"42\tmodule-null-sink\tsink_name={SINK_NAME} sink_properties=device.description=NetworkAudio\n"
        "99\tmodule-always-sink\t\n"
    )
    with patch("subprocess.run", return_value=MagicMock(stdout=sample_output)):
        assert router._verify_module_for_unload(42) is True
        # Wrong ID
        assert router._verify_module_for_unload(100) is False

    # 2. Wrong module type (e.g. module-always-sink recycled ID)
    recycled_output = f"42\tmodule-always-sink\tsink_name={SINK_NAME}\n"
    with patch("subprocess.run", return_value=MagicMock(stdout=recycled_output)):
        assert router._verify_module_for_unload(42) is False

    # 3. Wrong sink name (another application's null sink)
    other_sink_output = "42\tmodule-null-sink\tsink_name=other_app_sink\n"
    with patch("subprocess.run", return_value=MagicMock(stdout=other_sink_output)):
        assert router._verify_module_for_unload(42) is False


def test_remove_virtual_sink_skips_unload_if_module_recycled_or_mismatched(tmp_path):
    router = AudioRouter(state_dir=tmp_path)
    router.module_id = 42
    router.created_by_us = True
    router.previous_default_sink = "built_in_speaker"

    # Module 42 is now a different module (recycled PID/module ID)
    recycled_output = "42\tmodule-always-sink\t\n"

    def mock_run_impl(cmd, *args, **kwargs):
        if cmd == ["pactl", "list", "short", "modules"]:
            return MagicMock(stdout=recycled_output)
        return MagicMock(stdout="")

    with patch.object(router, "_sink_is_valid", return_value=True), \
         patch("subprocess.run", side_effect=mock_run_impl) as mock_run:

        router.remove_virtual_sink()

        calls = [c[0][0] for c in mock_run.call_args_list]
        # Must NOT call unload-module on the recycled module
        assert ["pactl", "unload-module", "42"] not in calls
        # Must still restore previous default sink
        assert ["pactl", "set-default-sink", "built_in_speaker"] in calls
        assert router.module_id is None
