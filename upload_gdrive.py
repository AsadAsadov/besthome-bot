import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def log(message: str) -> None:
    print(f"[GDRIVE] {message}")


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def credentials_path() -> Path:
    return script_dir() / "credentials.json"


def token_path() -> Path:
    return script_dir() / "token.json"


def last_link_path() -> Path:
    return script_dir() / "last_db_link.txt"


def notify_path() -> Path:
    return script_dir() / "notify.py"


def save_token(creds: Credentials) -> None:
    token_file = token_path()
    token_file.write_text(creds.to_json(), encoding="utf-8")
    log(f"Saved token: {token_file}")


def run_local_oauth_flow() -> Credentials:
    creds_file = credentials_path()
    if not creds_file.exists():
        raise RuntimeError(
            f"credentials.json not found at: {creds_file}. "
            "Create OAuth client credentials in Google Cloud Console and place the file next to this script."
        )

    log("Starting local OAuth flow in browser...")
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    creds = flow.run_local_server(port=0)
    save_token(creds)
    return creds


def _handle_invalid_grant_reauth() -> Credentials:
    token_file = token_path()
    if token_file.exists():
        revoked_backup = token_file.with_name("token.revoked.json")
        try:
            if revoked_backup.exists():
                revoked_backup.unlink()
            shutil.move(str(token_file), str(revoked_backup))
            log(f"Existing token moved to: {revoked_backup}")
        except OSError as exc:
            log(f"Could not backup token.json ({exc}); removing token.json instead")
            try:
                token_file.unlink(missing_ok=True)
            except OSError:
                pass

    log("Token refresh failed with invalid_grant. Re-authentication is required.")
    return run_local_oauth_flow()


def get_service() -> object:
    creds: Optional[Credentials] = None
    token_file = token_path()

    if token_file.exists():
        log(f"Loading token: {token_file}")
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception as exc:  # corrupted file, invalid format, etc.
            log(f"Failed to read token.json: {exc}")
            creds = None

    if creds and creds.valid:
        log("Using valid cached credentials")
        return build("drive", "v3", credentials=creds)

    if creds and creds.expired and creds.refresh_token:
        log("Credentials expired. Trying token refresh...")
        try:
            creds.refresh(Request())
            save_token(creds)
            log("Token refresh successful")
            return build("drive", "v3", credentials=creds)
        except RefreshError as exc:
            message = str(exc)
            log(f"Token refresh failed: {message}")
            if "invalid_grant" in message:
                creds = _handle_invalid_grant_reauth()
                return build("drive", "v3", credentials=creds)
            raise RuntimeError(
                "Google OAuth refresh failed. "
                "Please check your credentials, system clock, and OAuth client settings."
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                "Unexpected error while refreshing Google OAuth token. "
                "Try deleting token.json and rerun this script."
            ) from exc

    log("No valid token found. Starting OAuth login...")
    creds = run_local_oauth_flow()
    return build("drive", "v3", credentials=creds)


def upload_and_get_direct_link(zip_path: Path) -> str:
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP file not found: {zip_path}")

    service = get_service()
    metadata = {"name": zip_path.name}
    media = MediaFileUpload(str(zip_path), mimetype="application/zip", resumable=True)

    log(f"Uploading ZIP: {zip_path}")
    result = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    file_id = result["id"]
    log(f"Upload complete. file_id={file_id}")

    log("Setting file permission: anyone with link can read")
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    direct_link = f"https://drive.google.com/uc?export=download&id={file_id}"
    web_link = result.get("webViewLink")
    if web_link:
        log(f"webViewLink={web_link}")
    log(f"direct_link={direct_link}")
    return direct_link


def save_last_link_atomic(url: str) -> None:
    destination = last_link_path()
    temp_path = destination.with_suffix(destination.suffix + ".tmp")
    temp_path.write_text(f"{url}\n", encoding="utf-8")
    os.replace(str(temp_path), str(destination))
    log(f"Saved link atomically to: {destination}")


def notify_with_message(message: str) -> None:
    notify_script = notify_path()
    if notify_script.exists():
        try:
            subprocess.run(
                [sys.executable, str(notify_script), message],
                check=False,
            )
            log("notify.py executed")
        except Exception as exc:
            log(f"Failed to run notify.py: {exc}")
            print(f"NOTIFY MESSAGE: {message}")
    else:
        log("notify.py not found; printing message instead")
        print(f"NOTIFY MESSAGE: {message}")


def process_upload(zip_path: Path) -> int:
    try:
        direct_link = upload_and_get_direct_link(zip_path)
    except RefreshError as exc:
        error = (
            f"ERROR: Google OAuth failed after retry: {exc}. "
            "Action: delete token.json and rerun to re-authenticate."
        )
        log(error)
        notify_with_message(error)
        return 1
    except HttpError as exc:
        error = f"ERROR: Google Drive API error: {exc}"
        log(error)
        notify_with_message(error)
        return 1
    except Exception as exc:
        error = f"ERROR: Upload failed: {exc}"
        log(error)
        notify_with_message(error)
        return 1

    if direct_link:
        save_last_link_atomic(direct_link)
        notify_with_message(direct_link)
        return 0

    error = "ERROR: Upload finished but no download link was produced."
    log(error)
    notify_with_message(error)
    return 1


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upload ZIP to Google Drive and publish a direct download link."
    )
    parser.add_argument("zip_path", help="Path to ZIP file")
    args = parser.parse_args(argv)

    zip_path = Path(args.zip_path)
    return process_upload(zip_path)


if __name__ == "__main__":
    raise SystemExit(main())
