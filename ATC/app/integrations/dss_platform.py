from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ATC.app.core.config import settings


class DSSPlatformError(Exception):
    pass


@dataclass
class DSSAuth:
    token: str
    token_rate: int | None = None


class DSSPlatformClient:
    """Cliente minimo para Dahua DSS Platform API V8.x.

    Usa X-Subject-Token como exige la API. Para pruebas iniciales acepta
    DSS_STATIC_TOKEN; si no existe, intenta login con DSS_USERNAME/DSS_PASSWORD.
    """

    def __init__(self) -> None:
        self.base_url = (settings.dss_base_url or "").strip().rstrip("/")
        self.timeout = int(settings.dss_timeout_sec or 15)
        self.verify_ssl = bool(settings.dss_verify_ssl)
        if not self.base_url:
            raise DSSPlatformError("DSS_BASE_URL no configurado")

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def _token(self) -> str:
        static_token = (settings.dss_static_token or "").strip()
        if static_token:
            return static_token
        return self.login().token

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        use_token: bool = True,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        if use_token:
            headers["X-Subject-Token"] = self._token()
        try:
            response = requests.request(
                method,
                self._url(path),
                headers=headers,
                json=json_payload if json_payload is not None else {},
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            raise DSSPlatformError(f"Error de red llamando a DSS: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise DSSPlatformError(f"DSS respondio {response.status_code}: {response.text[:300]}") from exc

        if response.status_code >= 400 and not self._looks_like_auth_challenge(payload):
            raise DSSPlatformError(f"DSS respondio HTTP {response.status_code}: {payload}")
        code = payload.get("code")
        if code not in (None, 1000) and not self._looks_like_auth_challenge(payload):
            raise DSSPlatformError(f"DSS respondio codigo {code}: {payload.get('desc') or payload}")
        return payload

    @staticmethod
    def _looks_like_auth_challenge(payload: dict[str, Any]) -> bool:
        return bool(payload.get("realm") and payload.get("randomKey"))

    @staticmethod
    def _signature_md5(username: str, password: str, realm: str, random_key: str) -> str:
        temp1 = hashlib.md5(password.encode()).hexdigest()
        temp2 = hashlib.md5((username + temp1).encode()).hexdigest()
        temp3 = hashlib.md5(temp2.encode()).hexdigest()
        temp4 = hashlib.md5((username + ":" + realm + ":" + temp3).encode()).hexdigest()
        return hashlib.md5((temp4 + ":" + random_key).encode()).hexdigest()

    @staticmethod
    def _signature_sha256(username: str, password: str, realm: str, random_key: str) -> str:
        ha1 = hashlib.sha256(f"{username}:{realm}:{password}".encode()).hexdigest()
        ha2 = hashlib.sha256("POST:/brms/api/v1.0/accounts/authorize".encode()).hexdigest()
        return hashlib.sha256(f"{ha1}:{random_key}:{ha2}".encode()).hexdigest()

    @staticmethod
    def _rsa_encrypt_b64(text: str, public_key_b64: str) -> str:
        if not public_key_b64:
            return ""
        public_key_bytes = base64.b64decode(public_key_b64)
        public_key = serialization.load_der_public_key(public_key_bytes)
        encrypted = public_key.encrypt(text.encode(), padding.PKCS1v15())
        return base64.b64encode(encrypted).decode()

    def login(self) -> DSSAuth:
        username = (settings.dss_username or "").strip()
        password = (settings.dss_password or "").strip()
        if not username or not password:
            raise DSSPlatformError("DSS_USERNAME / DSS_PASSWORD no configurados")

        first = self._json_request(
            "POST",
            "/brms/api/v1.0/accounts/authorize",
            json_payload={"userName": username, "ipAddress": "", "clientType": "API_CLIENT"},
            use_token=False,
        )
        realm = str(first.get("realm") or "")
        random_key = str(first.get("randomKey") or "")
        if not realm or not random_key:
            raise DSSPlatformError(f"DSS no entrego desafio de autenticacion: {first}")

        support_encrypt_types = first.get("supportEncryptTypes") or []
        encrypt_type = str(support_encrypt_types[0] if support_encrypt_types else first.get("encryptType") or "MD5")
        if encrypt_type.upper() == "SHA256":
            signature = self._signature_sha256(username, password, realm, random_key)
            public_key = str(first.get("adaptivePublicKey") or first.get("publickey") or first.get("publicKey") or "")
        else:
            encrypt_type = "MD5"
            signature = self._signature_md5(username, password, realm, random_key)
            public_key = str(first.get("publickey") or first.get("publicKey") or "")

        aes_secret_key = str(uuid.uuid4()).replace("-", "")
        aes_secret_vector = str(uuid.uuid4()).replace("-", "")[-16:]
        second = self._json_request(
            "POST",
            "/brms/api/v1.0/accounts/authorize",
            json_payload={
                "mac": "",
                "signature": signature,
                "userName": username,
                "randomKey": random_key,
                "publicKey": "",
                "encryptType": encrypt_type,
                "ipAddress": "",
                "clientType": "API_CLIENT",
                "userType": "0",
                "secretKey": self._rsa_encrypt_b64(aes_secret_key, public_key),
                "secretVector": self._rsa_encrypt_b64(aes_secret_vector, public_key),
                "loginType": "1",
                "graphicVerifyCode": "",
            },
            use_token=False,
        )
        token = str(second.get("token") or (second.get("data") or {}).get("token") or "")
        if not token:
            raise DSSPlatformError(f"DSS no entrego token: {second}")
        token_rate_raw = second.get("tokenRate") or (second.get("data") or {}).get("tokenRate")
        try:
            token_rate = int(token_rate_raw) if token_rate_raw is not None else None
        except (TypeError, ValueError):
            token_rate = None
        return DSSAuth(token=token, token_rate=token_rate)

    def fetch_status(self, device_codes: list[str]) -> dict[str, Any]:
        return self._json_request(
            "POST",
            "/brms/api/v1.1/device/status/fetch/batch/list",
            json_payload={"deviceCodes": device_codes},
        )

    def capture_channel(self, channel_id: str) -> dict[str, Any]:
        return self._json_request("POST", f"/brms/api/v1.1/device/channel/{channel_id}/video/capture")

    def live_stream_url(self, channel_id: str, *, fmt: str, protocol: str, stream_type: int) -> dict[str, Any]:
        fmt = (fmt or "hls").strip().lower()
        if fmt not in {"hls", "flv"}:
            raise DSSPlatformError("Formato de stream invalido; use hls o flv")
        path = (
            f"/brms/api/v1.1/video/live/channel/{channel_id}/{fmt}"
            f"?protocol={protocol}&streamType={stream_type}"
        )
        return self._json_request("GET", path)

    def search_records(
        self,
        channel_id: str,
        *,
        start_time: str,
        end_time: str,
        stream_type: int,
        record_type: int,
        record_source: int,
        page: int,
    ) -> dict[str, Any]:
        token = self._token()
        return self._json_request(
            "POST",
            "/brms/api/v1.0/SS/Record/QueryRecords",
            json_payload={
                "data": {
                    "endTime": str(end_time),
                    "streamType": str(stream_type),
                    "recordType": str(record_type),
                    "recordSource": str(record_source),
                    "startTime": str(start_time),
                    "channelId": channel_id,
                    "page": str(page),
                    "session": token,
                }
            },
        )

    def playback_hls(
        self,
        channel_id: str,
        *,
        record_source: int,
        stream_id: str,
        record_type: int,
        stream_type: int,
        start_time: str,
        end_time: str,
        protocol: str,
    ) -> dict[str, Any]:
        path = (
            f"/brms/api/v1.1/video/playback/channel/{channel_id}/hls"
            f"?recordSource={record_source}&streamId={stream_id}&recordType={record_type}"
            f"&streamType={stream_type}&startTime={start_time}&endTime={end_time}&protocol={protocol}"
        )
        return self._json_request("GET", path)

    def playback_by_time(
        self,
        channel_id: str,
        *,
        ss_id: str,
        stream_id: str,
        record_source: int,
        record_type: int,
        stream_type: int,
        start_time: str,
        end_time: str,
        refer: int,
    ) -> dict[str, Any]:
        return self._json_request(
            "POST",
            "/brms/api/v1.0/SS/Playback/StartPlaybackByTime",
            json_payload={
                "data": {
                    "ssId": ss_id,
                    "recordType": str(record_type),
                    "streamType": str(stream_type),
                    "recordSource": str(record_source),
                    "channelId": channel_id,
                    "endTime": str(end_time),
                    "startTime": str(start_time),
                    "streamId": str(stream_id),
                    "enableRtsps": "0",
                    "fakeSdp": "0",
                    "refer": str(refer),
                }
            },
        )
