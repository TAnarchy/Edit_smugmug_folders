import sys
import json
import re
from datetime import datetime
from requests_oauthlib import OAuth1
import requests

# ────────────────────────────────────────────────
#  LOAD CREDENTIALS FROM config.json
# ────────────────────────────────────────────────

with open("config.json") as f:
    _cfg = json.load(f)

API_KEY             = _cfg["smugmug_api_key"]
API_SECRET          = _cfg["smugmug_api_secret"]
ACCESS_TOKEN        = _cfg["smugmug_access_token"]
ACCESS_TOKEN_SECRET = _cfg["smugmug_access_secret"]

DRY_RUN = False          # Set to False to actually create folder & move albums
VERBOSE = True

FOLDER_PARENT_PATH = "/Events"
NEW_FOLDER_URLNAME = "SIG-Events"
NEW_FOLDER_TITLE   = "SIG-Events"
NEW_FOLDER_PRIVACY = "Public"

# ──────── ADVANCED METADATA FILTERING ────────
# All conditions are AND (must all match)
FILTERS = {
    "Title_contains": "SIG",                # case-insensitive substring in Title
    # "Description_contains": "event",
    # "Keywords_contains": "wedding",
    # "Privacy": "Public",
    # "Date_after": "2023-01-01",
    # "Date_before": "2025-12-31",
    # "Min_images": 5,
}

# ────────────────────────────────────────────────

BASE_HOST = "https://api.smugmug.com"
BASE_URL  = f"{BASE_HOST}/api/v2"

auth = OAuth1(
    API_KEY,
    client_secret=API_SECRET,
    resource_owner_key=ACCESS_TOKEN,
    resource_owner_secret=ACCESS_TOKEN_SECRET,
    signature_method="HMAC-SHA1",
)

session = requests.Session()
session.auth = auth
session.headers.update({"Accept": "application/json"})


def api_get(url, params=None):
    if params is None:
        params = {}
    if VERBOSE:
        print(f"GET  {url}  {params}")
    r = session.get(url, params=params)
    r.raise_for_status()
    return r.json()


def api_post(url, json_data):
    if DRY_RUN:
        print(f"[DRY RUN] Would POST to {url}")
        print("   Payload:", json.dumps(json_data, indent=2))
        return {"Response": {"Node": {"Uri": url + " [dry-run]"}}}
    if VERBOSE:
        print(f"POST {url}")
    r = session.post(url, json=json_data)
    r.raise_for_status()
    return r.json()


def get_authuser():
    """Return (root_node_uri, nickname) for the authenticated user."""
    data = api_get(f"{BASE_URL}/!authuser")
    user = data["Response"]["User"]
    return user["Uris"]["Node"]["Uri"], user["NickName"]


def find_folder_in_children(parent_node_uri, urlname):
    """Search paginated children for a Folder with the given UrlName. Returns URI or None."""
    start = 1
    count = 100
    while True:
        children_data = api_get(f"{BASE_HOST}{parent_node_uri}!children",
                                params={"start": start, "count": count})
        children = children_data["Response"].get("Node", [])
        if isinstance(children, dict):
            children = [children]
        for node in children:
            if node.get("Type") == "Folder" and node.get("UrlName") == urlname:
                print(f"Using existing folder: {node['Name']} → {node.get('WebUri', '')}")
                return node["Uri"]
        paging = children_data["Response"].get("Pages", {})
        if not paging.get("NextPage"):
            break
        start += count
    return None


def find_or_create_folder(parent_node_uri, urlname, title, privacy="Public"):
    """Find or create a Folder node under parent_node_uri (/api/v2/node/... path)."""
    existing_uri = find_folder_in_children(parent_node_uri, urlname)
    if existing_uri:
        return existing_uri

    payload = {
        "Type":     "Folder",
        "UrlName":  urlname,
        "Name":     title,
        "Privacy":  privacy,
    }
    try:
        result = api_post(f"{BASE_HOST}{parent_node_uri}!children", json_data=payload)
        folder_uri = result["Response"]["Node"]["Uri"]
        print(f"Created folder: {folder_uri}")
        return folder_uri
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 409:
            print(f"409 Conflict — folder likely already exists. Response: {e.response.text[:400]}")
            # Re-scan children (may have been created just now or was missed)
            existing_uri = find_folder_in_children(parent_node_uri, urlname)
            if existing_uri:
                return existing_uri
            print("Could not locate the existing folder after 409. Cannot continue.")
        raise


def album_matches_filters(album):
    """Check if album satisfies all configured FILTERS (AND logic)."""
    for key, value in FILTERS.items():
        if key == "Title_contains":
            if not re.search(value, album.get("Title", ""), re.IGNORECASE):
                return False
        elif key == "Description_contains":
            if not re.search(value, album.get("Description", ""), re.IGNORECASE):
                return False
        elif key == "Keywords_contains":
            if not re.search(value, album.get("Keywords", ""), re.IGNORECASE):
                return False
        elif key == "Privacy":
            if album.get("Privacy") != value:
                return False
        elif key == "Date_after":
            album_date = album.get("Date")
            if not album_date or datetime.fromisoformat(album_date) < datetime.strptime(value, "%Y-%m-%d"):
                return False
        elif key == "Date_before":
            album_date = album.get("Date")
            if not album_date or datetime.fromisoformat(album_date) >= datetime.strptime(value, "%Y-%m-%d"):
                return False
        elif key == "Min_images":
            if album.get("ImageCount", 0) < value:
                return False
        else:
            print(f"Warning: Unknown filter key '{key}' — ignored.")
    return True


def find_albums_with_filters(nickname):
    """List all user albums and return those matching FILTERS."""
    albums = []
    start = 1
    count = 50
    filter_fields = "Title,Description,Keywords,Privacy,Date,ImageCount,WebUri,Uri,Uris"

    while True:
        params = {"_filter": filter_fields, "start": start, "count": count}
        data = api_get(f"{BASE_URL}/user/{nickname}!albums", params=params)
        page_albums = data["Response"].get("Album", [])
        if isinstance(page_albums, dict):
            page_albums = [page_albums]

        for alb in page_albums:
            if album_matches_filters(alb):
                albums.append({
                    "Title":      alb.get("Title", "(no title)"),
                    "Uri":        alb["Uri"],
                    "NodeUri":    alb.get("Uris", {}).get("Node", {}).get("Uri"),
                    "WebUri":     alb.get("WebUri", ""),
                    "Privacy":    alb.get("Privacy"),
                    "Date":       alb.get("Date"),
                    "ImageCount": alb.get("ImageCount", "?"),
                })

        paging = data.get("Response", {}).get("Pages", {})
        if not paging.get("NextPage"):
            break
        start += count

    return albums


def move_album_to_folder(album_uri, folder_api_path):
    """Move an album to a folder using the !movealbums endpoint."""
    payload = {"MoveUris": album_uri, "AutoRename": True}
    result = api_post(f"{BASE_URL}{folder_api_path}!movealbums", json_data=payload)
    print(f"Moved {album_uri}")
    return result


# ────────────────────────────────────────────────
#  MAIN
# ────────────────────────────────────────────────

def main():
    print("SmugMug Album Organizer – with metadata filtering")
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    if FILTERS:
        print("Active filters:")
        for k, v in FILTERS.items():
            print(f"  • {k}: {v}")
    print()

    # Authenticate and resolve canonical nickname
    root_node_uri, nickname = get_authuser()
    print(f"Authenticated as: {nickname}")
    print(f"Root node: {root_node_uri}\n")

    # Locate Events folder node via folder API
    events_data = api_get(f"{BASE_URL}/folder/user/{nickname}{FOLDER_PARENT_PATH}")
    events_node_uri = events_data["Response"]["Folder"]["Uris"]["Node"]["Uri"]
    print(f"Events folder node: {events_node_uri}\n")

    # Find or create the target subfolder under Events
    target_folder_node_uri = find_or_create_folder(
        events_node_uri,
        NEW_FOLDER_URLNAME,
        NEW_FOLDER_TITLE,
        privacy=NEW_FOLDER_PRIVACY,
    )
    folder_api_path = f"/folder/user/{nickname}{FOLDER_PARENT_PATH}/{NEW_FOLDER_URLNAME}"
    print(f"Target folder URI: {target_folder_node_uri}")
    print(f"Target folder API path: {folder_api_path}\n")

    # Find albums matching the filters
    matching_albums = find_albums_with_filters(nickname)
    print(f"\nFound {len(matching_albums)} matching albums:")
    for a in matching_albums:
        date_str = (a["Date"] or "")[:10]
        print(f"  • {a['Title']}  ({a['Privacy']}, {a['ImageCount']} imgs, {date_str})")
        print(f"    {a['WebUri']}")

    if not matching_albums:
        print("No albums match the filters.")
        return

    print("\nMoving albums...")
    for album in matching_albums:
        try:
            move_album_to_folder(album["Uri"], folder_api_path)
        except requests.exceptions.HTTPError as e:
            print(f"Error moving {album['Title']}: {e}")
            if e.response is not None:
                print(e.response.text[:400])

    print("\nDone!")


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
