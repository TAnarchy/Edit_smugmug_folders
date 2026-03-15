"""
update_gallery_settings.py

Finds every gallery (album) inside SmugMug folder:
    https://finephoto.smugmug.com/Events/SIG-Events/

Then patches each one with:
    - Shopping cart OFF  (Buyable = false)
    - Download OFF       (Downloadable = false)
    - Visibility         Unlisted  (Privacy = "Unlisted")

All API traffic is logged to api_requests.log via api_logger.py.
"""

import json
import sys
from requests_oauthlib import OAuth1
from api_logger import logged_session

# ── credentials ─────────────────────────────────────────────────────────────
with open("config.json") as f:
    _cfg = json.load(f)

API_KEY             = _cfg["smugmug_api_key"]
API_SECRET          = _cfg["smugmug_api_secret"]
ACCESS_TOKEN        = _cfg["smugmug_access_token"]
ACCESS_TOKEN_SECRET = _cfg["smugmug_access_secret"]

# ── target ───────────────────────────────────────────────────────────────────
FOLDER_PATH = "/Events/SIG-Events"   # relative to the user's root

# ── settings to apply ───────────────────────────────────────────────────────
PATCH_PAYLOAD = {
    "Privacy":        "Unlisted",
    "AllowDownloads": False,   # download off
    "Printable":      False,   # shopping cart / prints off (CanBuy is read-only)
}

DRY_RUN = False   # set True to preview without making changes

# ── HTTP session ─────────────────────────────────────────────────────────────
BASE_HOST = "https://api.smugmug.com"
BASE_URL  = f"{BASE_HOST}/api/v2"

session = logged_session()
session.auth = OAuth1(
    API_KEY,
    client_secret=API_SECRET,
    resource_owner_key=ACCESS_TOKEN,
    resource_owner_secret=ACCESS_TOKEN_SECRET,
    signature_method="HMAC-SHA1",
)
session.headers.update({"Accept": "application/json"})


# ── helpers ──────────────────────────────────────────────────────────────────
def api_get(url, params=None):
    r = session.get(url, params=params or {})
    r.raise_for_status()
    return r.json()


def api_patch(url, payload):
    if DRY_RUN:
        print(f"  [DRY RUN] PATCH {url}")
        print(f"  Payload: {json.dumps(payload)}")
        return {}
    r = session.patch(url, json=payload)
    r.raise_for_status()
    return r.json()


def get_nickname():
    """Resolve the canonical nickname for the authenticated user."""
    data = api_get(f"{BASE_URL}/!authuser")
    return data["Response"]["User"]["NickName"]


def get_albums_in_folder(nickname, folder_path):
    """
    Return all album dicts (with Uri, Name) inside the given folder path.
    Walks the folder's node tree children, paginating fully.
    Each returned dict has keys: Uri (album API URI), Name.
    """
    # Step 1: resolve the folder's Node URI
    folder_data = api_get(f"{BASE_URL}/folder/user/{nickname}{folder_path}")
    folder = folder_data["Response"]["Folder"]
    node_uri = folder["Uris"]["Node"]["Uri"]
    print(f"  Folder node URI : {node_uri}")

    # Step 2: paginate node children, collect Album-type nodes
    albums = []
    start = 1
    count = 100
    url = f"{BASE_HOST}{node_uri}!children"

    while True:
        data = api_get(url, params={"start": start, "count": count,
                                    "_filter": "Type,Name,UrlName,Uris"})
        nodes = data["Response"].get("Node", [])
        if isinstance(nodes, dict):
            nodes = [nodes]

        for node in nodes:
            if node.get("Type") == "Album":
                album_uri = node.get("Uris", {}).get("Album", {}).get("Uri")
                albums.append({
                    "Name": node.get("Name", node.get("UrlName", "?")),
                    "Uri":  album_uri,
                })

        pages = data["Response"].get("Pages", {})
        if not pages.get("NextPage"):
            break
        start += count

    return albums


def patch_album(album):
    """Apply PATCH_PAYLOAD to a single album. Returns (title, ok, message)."""
    title = album.get("Name") or album.get("Title") or album.get("UrlName", "?")
    album_uri = album.get("Uri", "")
    url = f"{BASE_HOST}{album_uri}"

    print(f"  Patching: {title}")
    print(f"    URI : {album_uri}")

    try:
        resp = api_patch(url, PATCH_PAYLOAD)
        if not DRY_RUN:
            updated = resp.get("Response", {}).get("Album", {})
            actual_privacy = updated.get("Privacy", "?")
            actual_dl      = updated.get("AllowDownloads", "?")
            actual_buy     = updated.get("Printable", "?")
            print(f"    OK  → Privacy={actual_privacy}, "
                  f"AllowDownloads={actual_dl}, Printable(cart)={actual_buy}")
        return title, True, "ok"
    except Exception as e:
        msg = str(e)
        print(f"    ERROR: {msg}")
        return title, False, msg


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("SmugMug Gallery Settings Updater")
    print(f"Folder : finephoto.smugmug.com{FOLDER_PATH}")
    print(f"Mode   : {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print(f"Apply  : {json.dumps(PATCH_PAYLOAD)}")
    print("=" * 60)

    nickname = get_nickname()
    print(f"Authenticated as: {nickname}\n")

    print(f"Fetching albums from {FOLDER_PATH} ...")
    albums = get_albums_in_folder(nickname, FOLDER_PATH)
    print(f"Found {len(albums)} album(s).\n")

    if not albums:
        print("Nothing to update.")
        return

    results = []
    for album in albums:
        title, ok, msg = patch_album(album)
        results.append((title, ok, msg))
        print()

    # Summary
    print("=" * 60)
    ok_count  = sum(1 for _, ok, _ in results if ok)
    err_count = len(results) - ok_count
    print(f"Done. {ok_count} updated, {err_count} failed.")
    if err_count:
        print("\nFailed albums:")
        for title, ok, msg in results:
            if not ok:
                print(f"  • {title}: {msg}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        sys.exit(1)
