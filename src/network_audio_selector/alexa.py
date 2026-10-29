from __future__ import annotations

import asyncio
import os
import shutil
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Speaker, SpeakerKind


class AlexaVerificationRequired(RuntimeError):
    """Amazon requires a one-time verification code to continue."""


def alexa_login_url(email: str) -> str:
    """Create an Amazon OAuth URL without handling the account password."""
    from alexapy import AlexaLogin

    async def build() -> str:
        login = AlexaLogin(
            os.environ.get("NETWORK_AUDIO_ALEXA_DOMAIN", "amazon.com"),
            email,
            "",
            lambda filename: filename,
        )
        try:
            return str(login.start_url)
        finally:
            await login.close()

    return asyncio.run(build())


@dataclass(frozen=True)
class AlexaConfig:
    email: str
    domain: str = "amazon.com"
    cookie_dir: Path = Path("~/.config/network-audio/alexa").expanduser()

    @classmethod
    def from_environment(cls) -> "AlexaConfig":
        email = os.environ.get("NETWORK_AUDIO_ALEXA_EMAIL", "").strip()
        if not email:
            raise RuntimeError(
                "Set NETWORK_AUDIO_ALEXA_EMAIL before using Alexa account control."
            )
        return cls(
            email=email,
            domain=os.environ.get("NETWORK_AUDIO_ALEXA_DOMAIN", "amazon.com").strip(),
            cookie_dir=Path(
                os.environ.get(
                    "NETWORK_AUDIO_ALEXA_COOKIE_DIR",
                    "~/.config/network-audio/alexa",
                )
            ).expanduser(),
        )

    @property
    def cookie_file(self) -> Path:
        return self.cookie_dir / f"alexa_media.{self.email}.cookies"

    def import_cookie(self, source: Path) -> Path:
        if not source.is_file():
            raise RuntimeError("The selected Alexa session file does not exist.")
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, self.cookie_file)
        self.cookie_file.chmod(0o600)
        return self.cookie_file


class AlexaBridge:
    """Optional account bridge for Alexa device and multi-room group control.

    alexapy uses an unofficial private Alexa web API. It can control devices and
    groups, but Amazon does not expose a public API for injecting arbitrary live
    audio into Echo devices.
    """

    def __init__(self, config: AlexaConfig) -> None:
        self.config = config
        self.login: Any | None = None
        self.devices: list[dict[str, Any]] = []

    async def connect(self) -> None:
        try:
            from alexapy import AlexaAPI, AlexaLogin
        except ImportError as exc:
            raise RuntimeError(
                "Alexa support is not installed. Install with: pip install '.[alexa]'"
            ) from exc

        self.config.cookie_dir.mkdir(parents=True, exist_ok=True)
        self.login = AlexaLogin(
            self.config.domain,
            self.config.email,
            "",
            lambda filename: str(self.config.cookie_dir / Path(filename).name),
        )
        cookies = await self.login.load_cookie()
        if not cookies:
            raise RuntimeError(
                "No Alexa session found. Authenticate with alexapy and save its "
                f"cookie file in {self.config.cookie_dir}."
            )
        await self.login.login(cookies=cookies)
        self.devices = await AlexaAPI.get_devices(self.login) or []

    async def connect_with_credentials(
        self, password: str, securitycode: str = ""
    ) -> None:
        try:
            from alexapy import AlexaAPI, AlexaLogin
        except ImportError as exc:
            raise RuntimeError(
                "Alexa support is not installed. Install with: pip install '.[alexa]'"
            ) from exc
        self.config.cookie_dir.mkdir(parents=True, exist_ok=True)
        self.login = AlexaLogin(
            self.config.domain,
            self.config.email,
            password,
            lambda filename: str(self.config.cookie_dir / Path(filename).name),
        )
        await self.login.login(
            data={"password": password, "securitycode": securitycode}
        )
        if not self.login.status.get("login_successful", False):
            status = self.login.status or {}
            if any(
                status.get(key)
                for key in (
                    "securitycode_required",
                    "verificationcode_required",
                    "claimspicker_required",
                    "authselect_required",
                    "approval",
                )
            ):
                raise AlexaVerificationRequired(
                    "Amazon sent a verification code. Enter it to continue."
                )
            detail = status.get("error_message") or status.get("message")
            message = "Amazon sign-in did not complete. Check your email and password."
            if detail:
                message += f" Amazon reported: {detail}"
            raise RuntimeError(message)
        self.devices = await AlexaAPI.get_devices(self.login) or []

    async def close(self) -> None:
        if self.login is not None:
            await self.login.close()
            self.login = None

    def targets(self) -> list[Speaker]:
        result: list[Speaker] = []
        for device in self.devices:
            serial = device.get("serialNumber")
            name = device.get("accountName") or device.get("deviceType")
            if not serial or not name:
                continue
            family = device.get("deviceFamily", "")
            kind = SpeakerKind.ALEXA
            note = "Alexa device"
            if family == "WHA":
                note = "Alexa multi-room group"
            result.append(
                Speaker(
                    id=f"alexa:{serial}",
                    name=str(name),
                    kind=kind,
                    address=None,
                    note=note,
                )
            )
        return sorted(result, key=lambda item: item.name.casefold())

    def _device(self, target: str) -> dict[str, Any]:
        wanted = target.casefold()
        for device in self.devices:
            if (
                str(device.get("serialNumber", "")).casefold() == wanted
                or str(device.get("accountName", "")).casefold() == wanted
            ):
                return device
        raise ValueError(f"Alexa target not found: {target}")

    async def set_volume(self, target: str, volume: float) -> None:
        from alexapy import AlexaAPI

        if self.login is None:
            raise RuntimeError("Alexa bridge is not connected")
        device = self._device(target)
        await AlexaAPI(device, self.login).set_volume(max(0.0, min(1.0, volume)))

    async def stop(self, target: str) -> None:
        from alexapy import AlexaAPI

        if self.login is None:
            raise RuntimeError("Alexa bridge is not connected")
        await AlexaAPI(self._device(target), self.login).stop()


def alexa_targets_sync() -> list[Speaker]:
    async def run() -> list[Speaker]:
        bridge = AlexaBridge(AlexaConfig.from_environment())
        try:
            await bridge.connect()
            return bridge.targets()
        finally:
            await bridge.close()

    return asyncio.run(run())


def alexa_sign_in_sync(
    email: str, password: str, securitycode: str = ""
) -> list[Speaker]:
    async def run() -> list[Speaker]:
        bridge = AlexaBridge(
            AlexaConfig(
                email=email,
                domain=os.environ.get("NETWORK_AUDIO_ALEXA_DOMAIN", "amazon.com"),
            )
        )
        try:
            await bridge.connect_with_credentials(password, securitycode)
            return bridge.targets()
        finally:
            await bridge.close()

    return asyncio.run(run())


def alexa_browser_sign_in_sync(email: str, redirect_url: str) -> list[Speaker]:
    async def run() -> list[Speaker]:
        from alexapy import AlexaAPI, AlexaLogin

        config = AlexaConfig(
            email=email,
            domain=os.environ.get("NETWORK_AUDIO_ALEXA_DOMAIN", "amazon.com"),
        )
        parsed = urlparse(redirect_url.strip())
        query = parse_qs(parsed.query)
        authorization_code = (
            query.get("openid.oa2.authorization_code", [None])[0]
            or query.get("authorization_code", [None])[0]
        )
        if not authorization_code:
            raise RuntimeError(
                "The Amazon redirect URL did not contain an authorization code."
            )
        config.cookie_dir.mkdir(parents=True, exist_ok=True)
        login = AlexaLogin(
            config.domain,
            email,
            "",
            lambda filename: str(config.cookie_dir / Path(filename).name),
        )
        try:
            login.authorization_code = authorization_code
            if not await login.get_tokens():
                raise RuntimeError("Amazon did not accept the browser login session.")
            if not await login.exchange_token_for_cookies():
                raise RuntimeError("Amazon did not return an Alexa session.")
            await login.get_csrf()
            await login.finalize_login()
            devices = await AlexaAPI.get_devices(login) or []
            bridge = AlexaBridge(config)
            bridge.login = login
            bridge.devices = devices
            return bridge.targets()
        finally:
            await login.close()

    return asyncio.run(run())


def alexa_proxy_sign_in_sync(email: str, timeout: int = 300) -> list[Speaker]:
    """Authenticate in the browser and capture the Alexa redirect locally."""

    async def run() -> list[Speaker]:
        from alexapy import AlexaAPI, AlexaLogin, AlexaProxy

        config = AlexaConfig(
            email=email,
            domain=os.environ.get("NETWORK_AUDIO_ALEXA_DOMAIN", "amazon.com"),
        )
        config.cookie_dir.mkdir(parents=True, exist_ok=True)
        login = AlexaLogin(
            config.domain,
            email,
            "",
            lambda filename: str(config.cookie_dir / Path(filename).name),
        )
        proxy = AlexaProxy(login, "http://127.0.0.1")
        try:
            await proxy.start_proxy(host="127.0.0.1")
            yield_url = str(proxy.access_url())
            import webbrowser

            webbrowser.open(yield_url)
            for _ in range(timeout):
                if login.authorization_code or login.access_token:
                    break
                await asyncio.sleep(1)
            else:
                raise TimeoutError(
                    "Alexa login timed out. Complete Amazon login in the browser and try again."
                )
            if not login.access_token and not await login.get_tokens():
                raise RuntimeError("Amazon did not accept the completed login.")
            if not await login.exchange_token_for_cookies():
                raise RuntimeError("Amazon did not return an Alexa session.")
            await login.get_csrf()
            await login.finalize_login()
            bridge = AlexaBridge(config)
            bridge.login = login
            bridge.devices = await AlexaAPI.get_devices(login) or []
            return bridge.targets()
        finally:
            await proxy.stop_proxy()
            await login.close()

    return asyncio.run(run())
