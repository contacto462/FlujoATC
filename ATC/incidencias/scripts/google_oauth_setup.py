from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    client_secret = root / "secrets" / "google_oauth_client_secret.json"
    token_file = root / "secrets" / "google_oauth_token.json"

    if not client_secret.exists():
        raise SystemExit(
            f"No existe {client_secret}. Descarga OAuth client secret (Desktop app) y guardalo con ese nombre."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    print(f"OK OAuth token guardado en: {token_file}")


if __name__ == "__main__":
    main()
