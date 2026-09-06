import base64
import json
import random
import re
import string
import time
import uuid
from urllib.parse import quote, unquote

import requests


def _jitter(lo: float = 3.0, hi: float = 8.0):
    time.sleep(random.uniform(lo, hi))


def _backoff(attempt: int):
    t = min(60 * (2 ** attempt), 600)
    time.sleep(t + random.uniform(0, t * 0.2))


class CleanerTool:
    BLOKS_VER = "dda8a25b49f5003a4b5be5546bb8ca6e9435a576c4cecf6a6f245f91eacc00ad"
    APP_ID = "567067343352427"
    UA = ("Instagram 400.0.0.49.68 Android (30/11; 420dpi; 1080x2198; "
          "samsung; SM-A705FN; a70q; qcom; ar_AE; 799297099)")
    CAPS = "3brTv10="

    def __init__(self, session_id: str):
        self.session_id = session_id.strip()
        uid = unquote(self.session_id).split(":")[0]
        self.user_id = uid if uid.isdigit() else None
        self.username = None
        self._sess = requests.Session()
        seed = uid if uid.isdigit() else "x"
        self._device_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "ig-dev-" + seed))
        self._android_id = "android-" + uuid.uuid5(uuid.NAMESPACE_DNS, "ig-aid-" + seed).hex[:16]
        self._family_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "ig-fam-" + seed))
        self._mid = None
        self._www_claim = "0"
        self.total_unliked = 0
        self.posts_unliked = 0
        self.reels_unliked = 0
        self.total_unreposted = 0
        self.total_unsaved = 0
        self.total_comments_deleted = 0
        self._repost_base = None
        self._comments_base = None

    def _report(self, text: str):
        print(text)

    def _progress(self, label: str, count: int):
        print(f"\r{label}: {count}", end="", flush=True)

    def _uid(self) -> str:
        return str(self.user_id or unquote(self.session_id).split(":")[0])

    def _headers(self) -> dict:
        uid = self._uid()
        bearer = "IGT:2:" + base64.b64encode(
            json.dumps({"ds_user_id": uid, "sessionid": self.session_id},
                       separators=(",", ":")).encode()).decode()
        h = {
            "authorization":         f"Bearer {bearer}",
            "user-agent":            self.UA,
            "content-type":          "application/x-www-form-urlencoded; charset=UTF-8",
            "accept-language":       "ar-AE, en-US",
            "accept-encoding":       "gzip, deflate",
            "x-ig-app-id":           self.APP_ID,
            "x-ig-app-locale":       "ar_AE",
            "x-ig-device-locale":    "ar_AE",
            "x-ig-mapped-locale":    "ar_AE",
            "x-bloks-version-id":    self.BLOKS_VER,
            "x-bloks-is-layout-rtl": "true",
            "x-ig-capabilities":     self.CAPS,
            "x-ig-connection-type":  "WIFI",
            "x-fb-connection-type":  "WIFI",
            "x-ig-device-id":        self._device_id,
            "x-ig-android-id":       self._android_id,
            "x-ig-family-device-id": self._family_id,
            "ig-intended-user-id":   uid,
            "ig-u-ds-user-id":       uid,
            "x-ig-www-claim":        self._www_claim,
            "x-fb-http-engine":      "Liger",
        }
        if self._mid:
            h["x-mid"] = self._mid
        return h

    def _capture(self, r):
        if r is None:
            return
        mid = r.headers.get("ig-set-x-mid")
        if mid and not self._mid:
            self._mid = mid
        claim = r.headers.get("x-ig-set-www-claim")
        if claim:
            self._www_claim = claim

    def _get(self, url: str, params=None):
        for attempt in range(5):
            try:
                r = self._sess.get(url, headers=self._headers(), params=params, timeout=30)
                self._capture(r)
                if r.status_code == 429:
                    _backoff(attempt)
                    continue
                return r
            except requests.RequestException:
                time.sleep(5)
        return None

    def _post(self, url: str, data=None):
        for attempt in range(5):
            try:
                r = self._sess.post(url, headers=self._headers(), data=data, timeout=30)
                self._capture(r)
                if r.status_code == 429:
                    _backoff(attempt)
                    continue
                return r
            except requests.RequestException:
                time.sleep(5)
        return None

    def _bk_context(self) -> str:
        return quote(json.dumps({"bloks_version": self.BLOKS_VER, "styles_id": "instagram"},
                                separators=(",", ":")))

    def login(self) -> bool:
        r = self._get("https://i.instagram.com/api/v1/accounts/current_user/?edit=true")
        if r is None:
            return False
        try:
            data = r.json()
        except Exception:
            return False
        user = data.get("user") if isinstance(data, dict) else None
        if user:
            self.user_id = user.get("pk") or self.user_id
            self.username = user.get("username")
            return bool(self.user_id)
        return False

    def _unlike_one(self, media_id: str, media_type: str) -> bool:
        payload = f"_uuid={self._device_id}&_uid={self._uid()}&radio_type=wifi-none"
        r = self._post(f"https://i.instagram.com/api/v1/media/{media_id}/unlike/", data=payload)
        if r is None:
            return False
        if r.status_code in (403, 404):
            _jitter(5, 12)
            r = self._post(f"https://i.instagram.com/api/v1/media/{media_id}/unlike/", data=payload)
            if r is None or r.status_code != 200:
                return False
        if r.status_code == 200:
            self.total_unliked += 1
            if media_type == "2":
                self.reels_unliked += 1
            else:
                self.posts_unliked += 1
            return True
        return False

    def _process_feed(self, url: str, default_type: str) -> int:
        count = 0
        max_id = None
        while True:
            r = self._get(url, params={"max_id": max_id} if max_id else {})
            if r is None:
                break
            try:
                data = r.json()
            except Exception:
                break
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                mid = item.get("id") or item.get("media", {}).get("id")
                mtype = str(item.get("media_type") or
                            item.get("media", {}).get("media_type", default_type))
                if mid and self._unlike_one(mid, mtype):
                    count += 1
                    if self.total_unliked % 5 == 0:
                        self._progress("Likes removed", self.total_unliked)
                _jitter(3.0, 7.0)
            max_id = data.get("next_max_id")
            if not max_id:
                break
            _jitter(5.0, 12.0)
        return count

    def run_unlike(self):
        self._report("Removing likes...")
        while True:
            posts = self._process_feed("https://i.instagram.com/api/v1/feed/liked/", "1")
            reels = self._process_feed("https://i.instagram.com/api/v1/clips/liked/", "2")
            if posts + reels == 0:
                break
            _jitter(8.0, 15.0)
        print()

    def _action_body(self, base: int, count: int, items_encoded: str) -> str:
        return "&".join([
            "main_filter_to_visible_on_facebook_value=0",
            "entrypoint=",
            "main_date_end_state_value=-1",
            f"number_of_items={count}",
            f"content_spinner_id={base + 2}",
            f"content_container_id={base}",
            "main_attribute_order_state_value=newest_to_oldest",
            f"items_for_action={items_encoded}",
            "main_authors_state_value=",
            "main_date_start_state_value=-1",
            f"_uuid={self._device_id}",
            f"content_element_id={base + 1}",
            "main_account_history_events_state_value=",
            "main_content_types_value=Posts%2C+Reels",
            f"bk_client_context={self._bk_context()}",
            "main_content_type_value=0",
            "shared_user_id=",
            f"bloks_versioning_id={self.BLOKS_VER}",
            "main_filter_to_visible_from_facebook_value=0",
            "main_order_state_value=1",
            "main_liked_privately_value=0",
            "main_includes_location_value=0",
        ])

    def _fetch_reposts(self) -> list:
        body = (f"_uuid={self._device_id}"
                f"&bk_client_context={self._bk_context()}"
                f"&bloks_versioning_id={self.BLOKS_VER}")
        r = self._post("https://i.instagram.com/api/v1/bloks/apps/"
                       "com.instagram.privacy.activity_center.media_repost_screen/", data=body)
        if r is None or r.status_code != 200:
            return []
        raw = r.text
        m = re.search(r'ReplaceEmbeddedChildV2, \(bk\.action\.i64\.Const, (\d+)\)', raw)
        if m:
            self._repost_base = int(m.group(1))
        ids = re.findall(r"\b(\d{15,20}_\d{8,15})\b", raw)
        if not ids:
            for ck in re.findall(r"ig_cache_key=([A-Za-z0-9%+/]+=*)", raw):
                try:
                    dec = base64.b64decode(unquote(ck).split(".")[0]).decode()
                    if dec.isdigit() and 15 <= len(dec) <= 20:
                        ids.append(dec)
                except Exception:
                    pass
        return list(dict.fromkeys(ids))

    def _delete_reposts(self, media_ids: list) -> bool:
        if self._repost_base is None:
            return False
        body = self._action_body(self._repost_base, len(media_ids), quote(",".join(media_ids)))
        for attempt in range(3):
            r = self._post("https://i.instagram.com/api/v1/bloks/apps/"
                           "com.instagram.privacy.activity_center.media_repost_delete/", data=body)
            if r is None:
                return False
            if r.status_code == 429:
                _backoff(attempt)
                continue
            if r.status_code == 200:
                self.total_unreposted += len(media_ids)
                return True
            return False
        return False

    def run_unrepost(self):
        self._report("Removing reposts...")
        seen = set()
        while True:
            ids = [i for i in self._fetch_reposts() if i not in seen]
            if not ids:
                break
            seen.update(ids)
            if not self._delete_reposts(ids):
                removed = 0
                for mid in ids:
                    if self._delete_reposts([mid]):
                        removed += 1
                        self._progress("Reposts removed", self.total_unreposted)
                    _jitter(3.0, 7.0)
                if removed == 0:
                    break
            else:
                self._progress("Reposts removed", self.total_unreposted)
            _jitter(5.0, 10.0)
        print()

    def _unsave_one(self, media_id: str) -> bool:
        sb = json.dumps({"_uuid": self._device_id, "_uid": self._uid(),
                         "radio_type": "wifi-none", "module_name": "feed_saved"},
                        separators=(",", ":"))
        r = self._post(f"https://i.instagram.com/api/v1/media/{media_id}/unsave/",
                       data="signed_body=SIGNATURE." + quote(sb, safe=""))
        return r is not None and r.status_code == 200

    def run_unsave(self):
        self._report("Removing saved posts...")
        max_id = None
        while True:
            url = "https://i.instagram.com/api/v1/feed/saved/posts/?count=12&include_feed_only=false"
            if max_id:
                url += f"&max_id={max_id}"
            r = self._get(url)
            if r is None or r.status_code != 200:
                break
            try:
                data = r.json()
            except Exception:
                break
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                media = item.get("media") or item
                mid = media.get("pk") or media.get("id")
                if mid and self._unsave_one(str(mid)):
                    self.total_unsaved += 1
                    if self.total_unsaved % 5 == 0:
                        self._progress("Saved posts removed", self.total_unsaved)
                _jitter(2.0, 5.0)
            max_id = data.get("next_max_id")
            if not max_id:
                break
            _jitter(5.0, 12.0)
        print()

    def _fetch_comments(self) -> list:
        body = (f"_uuid={self._device_id}"
                f"&bk_client_context={self._bk_context()}"
                f"&bloks_versioning_id={self.BLOKS_VER}")
        r = self._post("https://i.instagram.com/api/v1/bloks/apps/"
                       "com.instagram.privacy.activity_center.comments_screen/", data=body)
        if r is None or r.status_code != 200:
            return []
        raw = r.text
        m = re.search(r'ReplaceEmbeddedChildV2, \(bk\.action\.i64\.Const, (\d+)\)', raw)
        if m:
            self._comments_base = int(m.group(1))
        pattern = r'\\"(\d{15,20})\\",\s*\\"(\d{15,20})\\",\s*\\"[A-Za-z0-9_-]+\\",\s*\(bk\.action'
        pairs, seen = [], set()
        for post_id, comment_id in re.findall(pattern, raw):
            key = (post_id, comment_id)
            if key not in seen:
                seen.add(key)
                pairs.append(key)
        return pairs

    def _delete_comments(self, pairs: list) -> bool:
        if self._comments_base is None:
            return False
        items = [f"{post_id}:{comment_id}" for post_id, comment_id in pairs]
        body = self._action_body(self._comments_base, len(pairs), quote(",".join(items)))
        for attempt in range(3):
            r = self._post("https://i.instagram.com/api/v1/bloks/apps/"
                           "com.instagram.privacy.activity_center.comments_delete/", data=body)
            if r is None:
                return False
            if r.status_code == 429:
                _backoff(attempt)
                continue
            if r.status_code == 200:
                self.total_comments_deleted += len(pairs)
                return True
            return False
        return False

    def run_remove_comments(self):
        self._report("Removing comments...")
        seen = set()
        while True:
            pairs = [p for p in self._fetch_comments() if p not in seen]
            if not pairs:
                break
            seen.update(pairs)
            if not self._delete_comments(pairs):
                removed = 0
                for pair in pairs:
                    if self._delete_comments([pair]):
                        removed += 1
                        self._progress("Comments deleted", self.total_comments_deleted)
                    _jitter(3.0, 7.0)
                if removed == 0:
                    break
            else:
                self._progress("Comments deleted", self.total_comments_deleted)
            _jitter(5.0, 10.0)
        print()

    def run_former(self):
        pfp_urls = [
            "https://i.pinimg.com/550x/35/3f/c5/353fc517a4f4fac8d9ecfc734818e048.jpg",
            "https://i.pinimg.com/236x/c1/43/43/c1434392c4c11ac42b782e9397eb2b58.jpg",
            "https://i.pinimg.com/236x/0f/42/27/0f42279ce48796e63c920ba9aa0295a2.jpg",
            "https://i.pinimg.com/236x/bf/8d/0d/bf8d0d9df86c121ad4e9ed65b4bb92cb.jpg",
        ]
        # Download each picture once and reuse the bytes so every change is a
        # single upload round-trip, not a download + upload.
        images = []
        for url in pfp_urls:
            try:
                images.append(requests.get(url, timeout=10).content)
            except Exception:
                pass
        if not images:
            self._report("Could not load the profile pictures.")
            return

        self.former_changes = 0
        self.former_errors = 0
        self._report("Rotating profile picture...")
        while True:
            for content in images:
                csrf = "".join(random.choices(string.ascii_letters + string.digits, k=32))
                ok = False
                try:
                    r = requests.post(
                        "https://www.instagram.com/accounts/web_change_profile_picture/",
                        headers={
                            "User-Agent":       "Mozilla/5.0",
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer":          "https://www.instagram.com/accounts/edit/",
                            "X-CSRFToken":      csrf,
                            "Cookie":           f"sessionid={self.session_id}; csrftoken={csrf};",
                        },
                        files={"profile_pic": ("profile.jpg", content, "image/jpeg")},
                        timeout=15,
                    )
                    ok = r.status_code == 200 and r.json().get("status") == "ok"
                except Exception:
                    ok = False
                if ok:
                    self.former_changes += 1
                else:
                    # Rate limited or rejected — the only thing worth waiting on.
                    # Back off briefly, then keep going at full speed.
                    self.former_errors += 1
                    self._progress("Changes", self.former_changes)
                    time.sleep(30)
                    continue
                self._progress("Changes", self.former_changes)


def _run_former(session_id: str):
    tool = CleanerTool(session_id)
    if not tool.login():
        print("Failed to fetch account info.")
        return
    print(f"Logged in as @{tool.username}")
    print("\n[!] Remove your current profile picture before you start.")
    input("Press Enter once it is removed to begin...")

    tool.former_changes = tool.former_errors = 0
    try:
        tool.run_former()
        prefix = "Done!"
    except KeyboardInterrupt:
        prefix = "Stopped."

    print(f"\n{prefix}")
    print(f"Changes: {tool.former_changes}  Errors: {tool.former_errors}")


def _run_cleaner(session_id: str):
    print("\n[1] Remove Likes\n[2] Remove Reposts\n[3] Remove Saved Posts"
          "\n[4] Remove Comments\n[5] All of the above")
    raw = input("Choose (e.g. 1 or 1,3): ").strip()

    actions = {
        "1": ("Likes",    "run_unlike"),
        "2": ("Reposts",  "run_unrepost"),
        "3": ("Saved",    "run_unsave"),
        "4": ("Comments", "run_remove_comments"),
    }
    picks = list(actions) if raw == "5" else \
        [p.strip() for p in raw.split(",") if p.strip() in actions]
    if not picks:
        print("Invalid choice.")
        return

    tool = CleanerTool(session_id)
    if not tool.login():
        print("Failed to fetch account info.")
        return
    print(f"Logged in as @{tool.username}")

    try:
        for key in picks:
            getattr(tool, actions[key][1])()
        prefix = "Done!"
    except KeyboardInterrupt:
        prefix = "Stopped."

    print(f"\n{prefix}")
    if "1" in picks:
        print(f"Likes removed: {tool.total_unliked}  (Posts: {tool.posts_unliked}  Reels: {tool.reels_unliked})")
    if "2" in picks:
        print(f"Reposts removed: {tool.total_unreposted}")
    if "3" in picks:
        print(f"Saved posts removed: {tool.total_unsaved}")
    if "4" in picks:
        print(f"Comments deleted: {tool.total_comments_deleted}")


def main():
    print("Instagram Tool")
    print("\n[1] Former\n[2] Cleaner")
    mode = input("Choose: ").strip()
    session_id = input("\nSession ID: ").strip()

    if mode == "1":
        _run_former(session_id)
    else:
        _run_cleaner(session_id)


if __name__ == "__main__":
    main()
