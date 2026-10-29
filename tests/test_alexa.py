from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from network_audio_selector.alexa import AlexaConfig, alexa_login_url, alexa_browser_sign_in_sync
from network_audio_selector.workers import AlexaBrowserSignInWorker, AlexaSignInWorker, run_in_thread
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_alexa_config_supports_phone_numbers(tmp_path):
    # Test phone number with country code
    phone = "+919876543210"
    config = AlexaConfig(identifier=phone, domain="amazon.in", cookie_dir=tmp_path)
    assert config.identifier == phone
    assert config.email == phone
    assert config.domain == "amazon.in"
    # Safe file name without illegal path chars
    assert "alexa_media._919876543210.cookies" in str(config.cookie_file)


def test_alexa_config_from_environment(tmp_path):
    with patch.dict(os.environ, {"NETWORK_AUDIO_ALEXA_PHONE": "+911234567890", "NETWORK_AUDIO_ALEXA_DOMAIN": "amazon.in"}):
        config = AlexaConfig.from_environment()
        assert config.identifier == "+911234567890"
        assert config.domain == "amazon.in"


def test_alexa_login_url_with_phone_and_domain():
    fake_login = MagicMock()
    fake_login.start_url = "https://www.amazon.in/ap/signin?openid.mode=checkid_setup"
    fake_login.close = AsyncMock()

    with patch("alexapy.AlexaLogin", return_value=fake_login):
        url = alexa_login_url("+919876543210", domain="amazon.in")
        assert "amazon.in" in url


def test_alexa_browser_sign_in_url_parsing():
    sample_url = (
        "https://www.amazon.in/ap/maplanding?"
        "openid.oa2.authorization_code=ANTESTCODE12345&openid.mode=id_res"
    )

    fake_login = MagicMock()
    fake_login.get_tokens = AsyncMock(return_value=True)
    fake_login.exchange_token_for_cookies = AsyncMock(return_value=True)
    fake_login.get_csrf = AsyncMock(return_value=True)
    fake_login.finalize_login = AsyncMock(return_value=True)
    fake_login.close = AsyncMock()

    fake_api = MagicMock()
    fake_api.get_devices = AsyncMock(return_value=[
        {"serialNumber": "G090XG...", "accountName": "Living Room Echo", "deviceFamily": "ECHO"}
    ])

    with patch("alexapy.AlexaLogin", return_value=fake_login), \
         patch("alexapy.AlexaAPI", fake_api):
        speakers = alexa_browser_sign_in_sync("+919876543210", sample_url, domain="amazon.in")
        assert len(speakers) == 1
        assert speakers[0].name == "Living Room Echo"
        assert fake_login.authorization_code == "ANTESTCODE12345"


def test_run_in_thread_retains_worker(qapp):
    worker = MagicMock()
    thread, w = run_in_thread(worker)
    assert hasattr(thread, "_worker")
    assert thread._worker == worker
    thread.quit()
    thread.wait(500)
