import argparse
import os
import time
from typing import Optional

import dropbox
from dropbox.files import WriteMode
from dropbox.sharing import SharedLinkSettings

DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
if not DROPBOX_TOKEN:
    raise RuntimeError("DROPBOX_TOKEN is not set")


def _direct_download_url(shared_url: str) -> str:
    url = shared_url.replace("www.dropbox.com", "dl.dropboxusercontent.com")
    if "?" in url:
        url = url.split("?", 1)[0]
    return url


def _append_cache_buster(url: str) -> str:
    cache_buster = int(time.time())
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}cb={cache_buster}"


def _revoke_existing_links(dbx: dropbox.Dropbox, dropbox_path: str) -> None:
    result = dbx.sharing_list_shared_links(path=dropbox_path, direct_only=False)
    links = list(result.links)
    while result.has_more:
        result = dbx.sharing_list_shared_links(
            path=dropbox_path, direct_only=False, cursor=result.cursor
        )
        links.extend(result.links)
    for link in links:
        try:
            dbx.sharing_revoke_shared_link(link.url)
        except dropbox.exceptions.ApiError:
            pass


def upload_and_share(local_path: str, dropbox_path: str) -> str:
    dbx = dropbox.Dropbox(DROPBOX_TOKEN)
    with open(local_path, "rb") as f:
        metadata = dbx.files_upload(f.read(), dropbox_path, mode=WriteMode.overwrite)

    file_meta = dbx.files_get_metadata(dropbox_path)
    print(
        "[DROPBOX] uploaded rev={rev} size={size} modified={modified}".format(
            rev=getattr(file_meta, "rev", "-"),
            size=getattr(file_meta, "size", "-"),
            modified=getattr(file_meta, "server_modified", "-"),
        )
    )

    _revoke_existing_links(dbx, dropbox_path)

    shared_link = dbx.sharing_create_shared_link_with_settings(
        dropbox_path, settings=SharedLinkSettings()
    )
    print(f"[DROPBOX] shared_link={shared_link.url}")
    direct_url = _direct_download_url(shared_link.url)
    final_url = _append_cache_buster(direct_url)
    print(f"[DROPBOX] new_link={final_url}")

    output_path = os.path.join(os.path.dirname(__file__), "last_db_link.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_url)

    return final_url


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Upload a file to Dropbox.")
    parser.add_argument("local_path", help="Path to the local ZIP file")
    parser.add_argument("dropbox_path", help="Destination Dropbox path")
    args = parser.parse_args(argv)

    upload_and_share(args.local_path, args.dropbox_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
