import hmac
import json
import os
import re
import threading
from collections import deque
from datetime import datetime, timedelta
from functools import wraps
from itertools import chain, zip_longest
from operator import itemgetter
from urllib.parse import urlparse

import challonge
import pytz
import requests
from flask import Flask, Response, abort, jsonify, render_template, request, url_for

from bracket_view import build_bracket_data
from story import (SCREEN_MODES, Bracket, build_screen_data, build_upcoming, describe_match, find_screen_match,
                   live_label, match_choices, parse_time, suggestion_labels)

app = Flask(__name__)
app.jinja_env.add_extension("jinja2.ext.do")  # the bracket template uses {% do %}
NEXT_MATCH_START = timedelta(minutes=1)
MATCH_DELAY = timedelta(minutes=3)
timezone = pytz.timezone("America/Los_Angeles")
UserName = os.environ["CHALLONGE_USERNAME"]
APIKey = os.environ["CHALLONGE_API_KEY"]
challonge.set_credentials(UserName, APIKey)

# Tournaments, their vsbar rings, story screen picks and bracket colours are managed from /admin,
# along with what /live is showing. They're saved as a JSON file in Cloud Storage (CONFIG_BUCKET)
# so they survive restarts. Without CONFIG_BUCKET (local runs) they live in memory only and
# start from DEFAULT_TOURNAMENTS.
DEFAULT_TOURNAMENTS = [{"slug": "PongsTestTournament01", "ring": 1, "name": ""}]
DEFAULT_DISPLAY = {"background_color": "#00ff00", "title_color": "#FFFFFF", "round_title_bg_color": "#2C2C2C"}
CONFIG_BUCKET = os.environ.get("CONFIG_BUCKET", "")
CONFIG_OBJECT = os.environ.get("CONFIG_OBJECT", "tournaments.json")  # the dev service uses its own file
GCS_TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
# [{"slug", "ring": int or None, "name", "pinned_upset", "pinned_elimination", "display": {...}}], in display order
tournaments = []
tournament_ids = []
RING_TOURNAMENTS = {}  # vsbar ring number -> tournament slugs on that ring
live = None  # what /live shows: {"slug", "mode", "match_id"}, or None for a blank screen
saved_pause = None  # why Challonge calls are stopped, saved so a restart stays stopped; None when running
config_error = ""
_config_lock = threading.Lock()
_UNCHANGED = object()


def _gcs_headers():
    token = requests.get(GCS_TOKEN_URL, headers={"Metadata-Flavor": "Google"}, timeout=5).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def _gcs_read():
    """Return the saved tournament list, or None if nothing has been saved yet."""
    r = requests.get(
        f"https://storage.googleapis.com/storage/v1/b/{CONFIG_BUCKET}/o/{CONFIG_OBJECT}",
        params={"alt": "media"}, headers=_gcs_headers(), timeout=10,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _gcs_write(data):
    requests.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{CONFIG_BUCKET}/o",
        params={"uploadType": "media", "name": CONFIG_OBJECT},
        headers={**_gcs_headers(), "Content-Type": "application/json"},
        data=json.dumps(data, indent=2), timeout=10,
    ).raise_for_status()


def set_config(new_tournaments=None, new_live=_UNCHANGED, new_pause=_UNCHANGED, save=True):
    """Replace the tournament list, what's live and/or the saved pause. Saves first, so a failed save leaves things as they were."""
    global tournaments, tournament_ids, RING_TOURNAMENTS, live, saved_pause
    new_tournaments = tournaments if new_tournaments is None else new_tournaments
    new_live = live if new_live is _UNCHANGED else new_live
    new_pause = saved_pause if new_pause is _UNCHANGED else new_pause
    if new_live and new_live["slug"] not in {t["slug"] for t in new_tournaments}:
        new_live = None
    if save and CONFIG_BUCKET:
        _gcs_write({"tournaments": new_tournaments, "live": new_live, "paused": new_pause})
    rings = {}
    for t in new_tournaments:
        if t.get("ring"):
            rings.setdefault(t["ring"], []).append(t["slug"])
    tournaments, tournament_ids, RING_TOURNAMENTS = new_tournaments, [t["slug"] for t in new_tournaments], rings
    live, saved_pause = new_live, new_pause


def load_tournaments():
    global config_error
    data = None
    if CONFIG_BUCKET:
        try:
            data = _gcs_read()
        except Exception as e:
            config_error = f"Couldn't load saved tournaments ({e}). Using the defaults until you change the list."
            print(config_error)
    if isinstance(data, list):  # saved before /live existed
        data = {"tournaments": data, "live": None}
    if data is None:
        data = {"tournaments": [dict(t) for t in DEFAULT_TOURNAMENTS], "live": None}
    set_config(data["tournaments"], data.get("live"), data.get("paused"), save=False)


def parse_tournament_id(text):
    """Accept a bare Challonge ID or a tournament URL and return the API ID ('' if unrecognized)."""
    text = text.strip().rstrip("/")
    m = re.match(r"^(?:https?://)?([\w-]+)\.challonge\.com/(?:[a-z]{2}/)?([\w-]+)$", text)
    if m and m.group(1) != "www":
        return f"{m.group(1)}-{m.group(2)}"  # community/subdomain tournaments
    m = re.match(r"^(?:https?://)?(?:www\.)?challonge\.com/(?:[a-z]{2}/)?([\w-]+)$", text)
    if m:
        return m.group(1)
    return text if re.fullmatch(r"[\w-]+", text) else ""


load_tournaments()

# Every page that needs a tournament (overlays, VS bars, /live, brackets, admin) shares one copy of
# its data. A page reuses whatever another page pulled within CACHE_TTL, so each tournament costs at
# most one Challonge call per CACHE_TTL however many pages are open, and nothing while no page wants it.
CACHE_TTL = timedelta(seconds=20)
_cache = {}
_cache_lock = threading.Lock()

# Quota guard: if no Challonge data has changed for IDLE_PAUSE, stop calling Challonge
# and keep serving the last data until someone resumes from /admin.
# This lives in memory, so a fresh Cloud Run instance always starts active.
IDLE_PAUSE = timedelta(hours=1)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
_state = {
    "paused": False,
    "paused_at": None,
    "pause_reason": "",
    "last_change": datetime.now(timezone),
    "last_fetch": None,
    "api_calls": 0,
    "started": datetime.now(timezone),
}
if saved_pause:  # stopped before the restart, so stay stopped
    _state.update(paused=True, paused_at=_state["started"], pause_reason=saved_pause)
_call_times = deque()  # when each Challonge call in the last hour was made


class QueriesPaused(Exception):
    pass


def api(fn, *args, **params):
    """Make one Challonge call and count it against the quota."""
    _state["api_calls"] += 1
    _call_times.append(datetime.now(timezone))
    return fn(*args, **params)


def calls_last_hour():
    cutoff = datetime.now(timezone) - timedelta(hours=1)
    while _call_times and _call_times[0] < cutoff:
        _call_times.popleft()
    return len(_call_times)


def pause(reason):
    _state["paused"] = True
    _state["paused_at"] = datetime.now(timezone)
    _state["pause_reason"] = reason
    print(f"Challonge queries paused: {reason}")
    _save_pause(reason)


def resume():
    with _cache_lock:
        _state["paused"] = False
        _state["paused_at"] = None
        _state["pause_reason"] = ""
        # Start a fresh idle window so it doesn't immediately pause again.
        _state["last_change"] = datetime.now(timezone)
    print("Challonge queries resumed")
    _save_pause(None)


def _save_pause(reason):
    """Save the pause with the config, in the background since callers may hold the cache lock."""
    def save():
        with _config_lock:
            try:
                set_config(new_pause=reason)
            except Exception as e:
                print(f"Couldn't save the pause state: {e}")

    threading.Thread(target=save, daemon=True).start()


def cached(key, fetch):
    # Holding the lock during fetch means simultaneous requests wait for one call instead of each making their own.
    with _cache_lock:
        hit = _cache.get(key)
        now = datetime.now(timezone)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
        if not _state["paused"] and now - _state["last_change"] >= IDLE_PAUSE:
            pause(f"No changes in Challonge data for {IDLE_PAUSE}")
        if _state["paused"]:
            if hit:
                return hit[1]
            raise QueriesPaused(f"Queries paused; no saved data for {key}")
        value = fetch()
        _state["last_fetch"] = now
        if not hit or hit[1] != value:
            _state["last_change"] = now
        _cache[key] = (now, value)
        return value


def peek(key):
    """Return whatever is cached for key, however old, without calling Challonge."""
    with _cache_lock:
        hit = _cache.get(key)
    return hit[1] if hit else None


def cache_age(key):
    """Seconds since key was last fetched from Challonge, or None if it never was."""
    with _cache_lock:
        hit = _cache.get(key)
    return int((datetime.now(timezone) - hit[0]).total_seconds()) if hit else None


def fetch_tournament(slug):
    """One Challonge call: the tournament with all its matches and participants."""
    info = dict(api(challonge.tournaments.show, slug, include_matches=1, include_participants=1))
    # The included lists come back wrapped ({"match": {...}}); parse them the way matches.index would.
    return {
        "info": {k: v for k, v in info.items() if k not in ("matches", "participants")},
        "matches": challonge.api._parse(info.get("matches") or []),
        "participants": challonge.api._parse(info.get("participants") or []),
    }


def load_tournament(slug):
    """(info, bracket) for a tournament, from the shared copy or one Challonge call if it's older than CACHE_TTL."""
    data = cached(("tournament", slug), lambda: fetch_tournament(slug))
    return data["info"], data


def peek_tournament(slug):
    """(info, bracket) from the shared copy only, or (None, None). Never calls Challonge."""
    data = peek(("tournament", slug))
    return (data["info"], data) if data else (None, None)


def get_all_matches(bracket):
    matches = bracket["matches"]
    participants = bracket["participants"]
    participants_dict = {p["id"]: p["name"] for p in participants}

    prerequisite_matches = {}
    for m in matches:
        if m.get("player1_prereq_match_id"):
            prerequisite_matches.setdefault(m["player1_prereq_match_id"], []).append(
                m["id"]
            )
        if m.get("player2_prereq_match_id"):
            prerequisite_matches.setdefault(m["player2_prereq_match_id"], []).append(
                m["id"]
            )

    relevant_matches = []
    for match in matches:
        match_id = match["id"]
        next_ids = prerequisite_matches.get(match_id)
        nextPlayerName = None
        nextMatchId = None

        if next_ids:
            next_match = next((m for m in matches if m["id"] == next_ids[0]), None)
            if next_match:
                next_player_id = next_match.get("player1_id") or next_match.get(
                    "player2_id"
                )
                nextPlayerName = (
                    "Next Opponent: " + participants_dict.get(next_player_id)
                    if next_player_id
                    else ""
                )
                nextMatchId = next_ids[0]

        if match["state"] != "complete" and (
            match.get("player1_id") or match.get("player2_id")
        ):
            relevant_matches.append(
                {
                    "id": match["id"],
                    "tournament_id": match["tournament_id"],
                    "state": match["state"],
                    "player1_id": match["player1_id"],
                    "player1_name": participants_dict.get(
                        match.get("player1_id"), None
                    ),
                    "player2_id": match["player2_id"],
                    "player2_name": participants_dict.get(
                        match.get("player2_id"), None
                    ),
                    "player1_prereq_match_id": match["player1_prereq_match_id"],
                    "player2_prereq_match_id": match["player2_prereq_match_id"],
                    "created_at": match["created_at"],
                    "updated_at": match["updated_at"],
                    "round": match["round"],
                    "suggested_play_order": match["suggested_play_order"],
                    "next_player_match": nextMatchId,
                    "next_player_name": nextPlayerName,
                    "underway_at": match["underway_at"],
                }
            )
    return relevant_matches


def interleave_matches(matches):
    # Group matches by tournament_id
    grouped_matches = {}
    for match in matches:
        grouped_matches.setdefault(match["tournament_id"], []).append(match)

    # Sort each group by suggested_play_order
    for tournament_id in grouped_matches:
        grouped_matches[tournament_id].sort(key=lambda x: x["suggested_play_order"])

    # Interleave matches from each tournament in round-robin style
    interleaved_matches = []
    while any(grouped_matches.values()):
        for tournament_id in list(grouped_matches):  # Use list to avoid RuntimeError
            if grouped_matches[tournament_id]:
                interleaved_matches.append(grouped_matches[tournament_id].pop(0))
            else:
                del grouped_matches[tournament_id]

    return interleaved_matches


def most_recent_match_time(tournament):
    most_recent_match_time = datetime.min.replace(tzinfo=timezone)
    try:
        for m in tournament.get("matches", []):
            if m.get("state") != "complete":
                continue
            match_time = m.get("updated_at")
            if isinstance(match_time, str):
                match_time = datetime.fromisoformat(match_time)
            if isinstance(match_time, datetime):
                if match_time.tzinfo is None:
                    match_time = match_time.replace(tzinfo=timezone)
                if match_time > most_recent_match_time:
                    most_recent_match_time = match_time
    except Exception as e:
        print(f"Error processing match times: {e}")
    return most_recent_match_time


def fetch_pending_matches(tournaments):
    pending_matches = []
    for tournament in tournaments.values():
        for match in tournament.get("matches", []):
            if match["state"] == "pending":
                pending_matches.append(match)
    return pending_matches


@app.route("/")
@app.route("/current_matches")
def index():
    return render_template("current_matches.html")


def filter_next_matches_per_participant(matches):
    """Filter to show only the next match for each participant in round robin tournaments"""
    participant_next_match = {}

    # Group matches by tournament to handle each separately
    tournament_groups = {}
    for match in matches:
        tournament_id = match.get("tournament", "")
        if tournament_id not in tournament_groups:
            tournament_groups[tournament_id] = []
        tournament_groups[tournament_id].append(match)

    filtered_matches = []

    for tournament_id, tournament_matches in tournament_groups.items():
        # Check if this is a round robin by looking for matches with next opponents
        has_next_opponents = any(
            match.get("NxtName", "") != "" for match in tournament_matches
        )

        if has_next_opponents:
            # For tournaments with next opponents, show all matches with next opponents
            filtered_matches.extend(
                [
                    match
                    for match in tournament_matches
                    if match.get("NxtName", "") != ""
                ]
            )
        else:
            # For round robin tournaments, show only next match per participant
            participant_next_match = {}

            # Sort matches by suggested play order to find the next match for each participant
            sorted_matches = sorted(
                tournament_matches, key=lambda x: x.get("suggested_play_order", 0)
            )

            for match in sorted_matches:
                player1 = match.get("player1", "")
                player2 = match.get("player2", "")

                # Only consider matches that are open or pending
                if match.get("status") in ["open", "pending"] and player1 and player2:
                    # Add this match as the next match for both participants if they don't have one yet
                    if player1 not in participant_next_match:
                        participant_next_match[player1] = match
                    if player2 not in participant_next_match:
                        participant_next_match[player2] = match

            # Add unique matches to filtered list
            added_match_ids = set()
            for match in participant_next_match.values():
                match_id = match.get("MatchId")
                if match_id not in added_match_ids:
                    filtered_matches.append(match)
                    added_match_ids.add(match_id)

    return filtered_matches


@app.route("/filtered_matches")
def filtered_matches():
    try:
        all_data = generate_json_from_matches_by_state("all")
        if not all_data:
            print("WARNING: No data returned")
            filtered_matches = []
        else:
            all_matches = all_data.get_json()
            filtered_matches = filter_next_matches_per_participant(all_matches)
    except Exception as e:
        print(f"ERROR: Failed to generate filtered matches: {e}")
        filtered_matches = []
    return render_template("filtered_matches.html", matches=filtered_matches)


@app.route("/filtered_matches.json")
def raw_filtered_matches():
    try:
        all_data = generate_json_from_matches_by_state("all")
        if not all_data:
            print("WARNING: No data returned")
            filtered_matches = []
        else:
            all_matches = all_data.get_json()
            filtered_matches = filter_next_matches_per_participant(all_matches)
    except Exception as e:
        print(f"ERROR: Failed to generate filtered matches: {e}")
        filtered_matches = []
    return jsonify(filtered_matches)


@app.context_processor
def inject_paused():
    # Server-rendered pages show the red paused dot from this.
    return {"queries_paused": _state["paused"]}


@app.after_request
def add_paused_header(response):
    # Polling pages read this to show or hide the red paused dot without reloading.
    response.headers["X-Queries-Paused"] = "1" if _state["paused"] else "0"
    return response


def admin_authorized():
    auth = request.authorization
    return bool(ADMIN_PASSWORD) and auth is not None and hmac.compare_digest(
        (auth.password or "").encode(), ADMIN_PASSWORD.encode()
    )


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not ADMIN_PASSWORD:
            return Response("Admin page is disabled: ADMIN_PASSWORD is not set.", 503)
        if not admin_authorized():
            return Response(
                "Password required.", 401, {"WWW-Authenticate": 'Basic realm="MatchManager admin"'}
            )
        # The browser resends the password automatically, so refuse form posts from other sites.
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and urlparse(origin).netloc != request.host:
            return Response("Cross-site request refused.", 403)
        return view(*args, **kwargs)

    return wrapped


SCREEN_LABELS = {"overview": "Overview", "upset": "Upset watch", "elimination": "Elimination"}
STATUS_LABELS = {"pending": "Not started", "complete": "Complete"}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def fmt(t):
    return t.strftime("%b %d, %I:%M:%S %p %Z") if t else "never"


def admin_state():
    """Everything the admin page shows, as JSON-ready data. Uses the shared copy only, so it never calls Challonge."""
    now = datetime.now(timezone)
    idle_left = None
    if not _state["paused"]:
        idle_left = str(max(IDLE_PAUSE - (now - _state["last_change"]), timedelta(0))).split(".")[0]
    rows, live_info = [], None
    for t in tournaments:
        slug = t["slug"]
        info, bracket = peek_tournament(slug)
        pins = {"upset": t.get("pinned_upset"), "elimination": t.get("pinned_elimination")}
        choices = {"upset": [], "elimination": []}
        if info and bracket:
            try:
                choices = match_choices(bracket["matches"], bracket["participants"], info.get("tournament_type"), pins)
            except Exception as e:
                print(f"Couldn't list story screen matches for {slug}: {e}")
        # A pin that's no longer offered (the match was fought) means auto again.
        picks = {mode: pins[mode] if any(c["id"] == pins[mode] for c in choices[mode]) else None for mode in choices}
        name = t.get("name") or (info or {}).get("name") or slug
        rows.append({
            "slug": slug,
            "name": name,
            "ring": t.get("ring"),
            "status": STATUS_LABELS.get(info.get("state"), "In progress") if info else "No data yet",
            "age": cache_age(("tournament", slug)),
            "display": {**DEFAULT_DISPLAY, **t.get("display", {})},
            "choices": choices,
            "picks": picks,
        })
        if live and live["slug"] == slug:
            label = ""
            if live.get("match_id") and info and bracket:
                label = describe_match(bracket["matches"], bracket["participants"], info.get("tournament_type"),
                                       live["match_id"]) or ""
            live_info = {**live, "tournament": name, "screen": SCREEN_LABELS[live["mode"]], "match": label}
    return {
        "status": {
            "paused": _state["paused"],
            "pause_reason": _state["pause_reason"],
            "paused_at": fmt(_state["paused_at"]),
            "idle_left": idle_left,
            "idle_pause": str(IDLE_PAUSE),
            "api_calls": _state["api_calls"],
            "calls_last_hour": calls_last_hour(),
            "started": fmt(_state["started"]),
            "last_change": fmt(_state["last_change"]),
            "last_change_time": _state["last_change"].strftime("%-I:%M %p"),
        },
        "live": live_info,
        "tournaments": rows,
        "rings": sorted(RING_TOURNAMENTS),
        "config_error": config_error,
        "saved": bool(CONFIG_BUCKET),
    }


@app.route("/admin")
@require_admin
def admin():
    return render_template("admin.html", state=admin_state())


@app.route("/admin/api/state")
@require_admin
def admin_api_state():
    return jsonify(admin_state())


class AdminError(Exception):
    pass


def admin_action(view):
    """JSON admin endpoint: runs the change under the config lock and answers with the new page state."""
    @wraps(view)
    def wrapped():
        try:
            with _config_lock:
                view(request.get_json(silent=True) or {})
        except AdminError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            print(f"Admin change failed: {e}")
            return jsonify({"error": f"Couldn't save the change: {e}"}), 500
        return jsonify(admin_state())

    return wrapped


def find_tournament(slug):
    t = next((t for t in tournaments if t["slug"] == slug), None)
    if t is None:
        raise AdminError(f"{slug} isn't on the list.")
    return t


def update_tournament(slug, **changes):
    find_tournament(slug)
    set_config([dict(t, **changes) if t["slug"] == slug else t for t in tournaments])


def parse_ring(value):
    value = str(value if value is not None else "").strip()
    if not value:
        return None
    if not value.isdigit() or not 1 <= int(value) <= 99:
        raise AdminError("Ring must be a number from 1 to 99, or blank for none.")
    return int(value)


def parse_match_id(value):
    if value in (None, ""):
        return None
    if not str(value).isdigit():
        raise AdminError("Pick a match from the list.")
    return int(value)


def reserved_slugs():
    """First path segments the app already uses, which a tournament ID can't take."""
    return {r.rule.strip("/").split("/")[0].lower() for r in app.url_map.iter_rules()} - {""}


@app.route("/admin/api/add", methods=["POST"])
@require_admin
@admin_action
def admin_add_tournament(data):
    global config_error
    slug = parse_tournament_id(data.get("tournament", ""))
    if not slug:
        raise AdminError("Enter a Challonge tournament ID or URL.")
    ring = parse_ring(data.get("ring"))
    if slug in tournament_ids:
        raise AdminError(f"{slug} is already on the list.")
    if slug.lower() in reserved_slugs():
        raise AdminError(f"{slug} clashes with one of this app's own pages, so its bracket URL wouldn't work.")
    # One Challonge call catches typos and gets the name, and its data is kept for the pages to share.
    try:
        data = fetch_tournament(slug)
    except Exception as e:
        raise AdminError(f"Challonge couldn't find {slug}: {e}")
    set_config(tournaments + [{"slug": slug, "ring": ring, "name": data["info"].get("name", ""),
                               "display": dict(DEFAULT_DISPLAY)}])
    with _cache_lock:
        _cache[("tournament", slug)] = (datetime.now(timezone), data)
    config_error = ""


@app.route("/admin/api/remove", methods=["POST"])
@require_admin
@admin_action
def admin_remove_tournament(data):
    slug = data.get("slug", "")
    find_tournament(slug)
    set_config([t for t in tournaments if t["slug"] != slug])
    with _cache_lock:
        _cache.pop(("tournament", slug), None)


@app.route("/admin/api/move", methods=["POST"])
@require_admin
@admin_action
def admin_move_tournament(data):
    slug, step = data.get("slug", ""), data.get("step")
    find_tournament(slug)
    if step not in (-1, 1):
        raise AdminError("Can only move a tournament up or down one place.")
    order = list(tournaments)
    i = next(i for i, t in enumerate(order) if t["slug"] == slug)
    j = i + step
    if 0 <= j < len(order):
        order[i], order[j] = order[j], order[i]
        set_config(order)


@app.route("/admin/api/ring", methods=["POST"])
@require_admin
@admin_action
def admin_set_ring(data):
    update_tournament(data.get("slug", ""), ring=parse_ring(data.get("ring")))


@app.route("/admin/api/display", methods=["POST"])
@require_admin
@admin_action
def admin_set_display(data):
    slug, key, value = data.get("slug", ""), data.get("key"), data.get("value", "")
    if key not in DEFAULT_DISPLAY or not HEX_COLOR.match(value):
        raise AdminError("Pick a colour.")
    display = {**DEFAULT_DISPLAY, **find_tournament(slug).get("display", {}), key: value}
    update_tournament(slug, display=display)


@app.route("/admin/api/pick", methods=["POST"])
@require_admin
@admin_action
def admin_pick_match(data):
    mode = data.get("mode")
    if mode not in ("upset", "elimination"):
        raise AdminError(f"Unknown story screen {mode}.")
    update_tournament(data.get("slug", ""), **{f"pinned_{mode}": parse_match_id(data.get("match_id"))})


@app.route("/admin/api/go", methods=["POST"])
@require_admin
@admin_action
def admin_go_live(data):
    slug, mode = data.get("slug", ""), data.get("mode")
    if mode not in SCREEN_MODES:
        raise AdminError(f"Unknown story screen {mode}.")
    t = find_tournament(slug)
    match_id = None
    if mode != "overview":
        match_id = parse_match_id(data.get("match_id"))
        try:
            info, bracket = load_tournament(slug)
        except Exception:
            info, bracket = peek_tournament(slug)
        label = data.get("match")
        if match_id is None and label and info and bracket:
            # Companion names the fight by its /live label ("LR7 · #66") instead of the dropdown's id.
            match_id = find_screen_match(bracket["matches"], bracket["participants"], info.get("tournament_type"),
                                         mode, label)
            if match_id is None:
                raise AdminError(f"{label} isn't a fight the {SCREEN_LABELS[mode]} screen can show right now.")
        elif match_id is None or not info or not bracket:
            raise AdminError("Nothing is ready for that screen yet.")
        else:
            # Only what that screen's dropdown offers, so an upset-only fight can't go out as an elimination.
            pins = {"upset": t.get("pinned_upset"), "elimination": t.get("pinned_elimination")}
            offered = match_choices(bracket["matches"], bracket["participants"], info.get("tournament_type"), pins)
            if not any(c["id"] == match_id for c in offered[mode]):
                raise AdminError("That fight isn't ready any more.")
    set_config(new_live={"slug": slug, "mode": mode, "match_id": match_id})


@app.route("/admin/api/off", methods=["POST"])
@require_admin
@admin_action
def admin_off_air(data):
    # A blank /live is plain chroma green and stops pulling that tournament's data.
    set_config(new_live=None)


@app.route("/admin/api/refresh", methods=["POST"])
@require_admin
@admin_action
def admin_refresh_tournament(data):
    slug = data.get("slug", "")
    find_tournament(slug)
    try:
        load_tournament(slug)
    except QueriesPaused:
        raise AdminError("Challonge queries are paused. Resume them to refresh.")
    except Exception as e:
        raise AdminError(f"Couldn't refresh {slug} from Challonge: {e}")


@app.route("/admin/api/pause", methods=["POST"])
@require_admin
@admin_action
def admin_pause(data):
    with _cache_lock:
        pause("Stopped from the admin page")


@app.route("/admin/api/resume", methods=["POST"])
@require_admin
@admin_action
def admin_resume(data):
    resume()


def wrap_index(value, count):
    """A knob position (any whole number, even negative) as an index into a list of count things."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    return n % count if count else 0


@app.route("/admin/api/companion")
@require_admin
def admin_api_companion():
    """Flat values for Bitfocus Companion knobs. ?t= picks the tournament and ?m= the suggested fight, both
    wrapping around, for the screen in ?mode=. Uses the shared copy only, so polling it never calls Challonge."""
    mode = request.args.get("mode", "overview")
    if mode not in SCREEN_MODES:
        return jsonify({"error": f"Unknown story screen {mode}."}), 400
    t_index = wrap_index(request.args.get("t"), len(tournaments))
    t = tournaments[t_index] if tournaments else {}
    slug = t.get("slug", "")
    info, bracket = peek_tournament(slug) if slug else (None, None)
    fights = []
    if mode != "overview" and info and bracket:
        pins = {"upset": t.get("pinned_upset"), "elimination": t.get("pinned_elimination")}
        try:
            fights = suggestion_labels(bracket["matches"], bracket["participants"], info.get("tournament_type"),
                                       pins)[mode]
        except Exception as e:
            print(f"Couldn't list Companion suggestions for {slug}: {e}")
    m_index = wrap_index(request.args.get("m"), len(fights))
    fight = fights[m_index] if fights else {"label": "", "fighters": ""}
    on_air = {"slug": "", "mode": "", "match": ""}
    if live:
        on_air = {"slug": live["slug"], "mode": live["mode"], "match": ""}
        live_info, live_bracket = peek_tournament(live["slug"])
        if live.get("match_id") and live_info and live_bracket:
            b = Bracket(live_bracket["matches"], live_bracket["participants"], live_info.get("tournament_type"))
            m = b.by_id.get(live["match_id"])
            on_air["match"] = live_label(b, m) if m else ""
    return jsonify({
        "t": t_index,
        "tournament_count": len(tournaments),
        "slug": slug,
        "name": t.get("name") or (info or {}).get("name") or slug,
        "mode": mode,
        "screen": SCREEN_LABELS[mode],
        "m": m_index,
        "match_count": len(fights),
        "match": fight["label"],
        "fighters": fight["fighters"],
        "live_slug": on_air["slug"],
        "live_mode": on_air["mode"],
        "live_match": on_air["match"],
    })


@app.route("/live")
def live_screen():
    """The one OBS browser source for story screens. Shows whatever was last pushed live from /admin."""
    return render_template("story_screen.html", tournament_name="Live", background="#00ff00",
                           data_url=url_for("live_data"), refresh_ms=1000)


@app.route("/live/data")
def live_data():
    current = live
    if not current:
        return jsonify({"blank": True, "key": "", "background": "#00ff00"})
    slug = current["slug"]
    t = next((t for t in tournaments if t["slug"] == slug), {})
    try:
        info, bracket = load_tournament(slug)
        data = build_screen_data(info.get("name") or t.get("name") or slug, bracket["matches"],
                                 bracket["participants"], info.get("tournament_type"), current["mode"],
                                 pinned_match_id=current.get("match_id"), strict=True)
    except Exception as e:
        print(f"Failed to build the live story screen for {slug}: {e}")
        return jsonify({"error": str(e)}), 503
    data["key"] = f'{slug}:{current["mode"]}:{current.get("match_id")}'
    return jsonify(data)


@app.route("/styles.css")
def static_file():
    return app.send_static_file("styles.css")


@app.route("/matches_data.json")
def matches_data():
    return generate_json_from_matches_by_state("all")


@app.route("/pending_matches.json")
def pending_matches():
    return generate_json_from_matches_by_state("pending")


@app.route("/open_matches.json")
def open_matches():
    return generate_json_from_matches_by_state("open")


@app.route("/upcoming.json")
def upcoming_data():
    """Data for the Upcoming page (/current_matches): next fights across every tournament on the list."""
    rows = []
    for slug in tournament_ids:
        try:
            info, bracket = load_tournament(slug)
            if not info:
                continue
            rows.append((info.get("name", slug), bracket["matches"], bracket["participants"],
                         info.get("tournament_type")))
        except Exception as e:
            print(f"Failed to load tournament {slug} for upcoming: {e}")
    return jsonify(build_upcoming(rows))


@app.route("/vsbar")
def vsbar():
    return vsbar_by_index(1)


@app.route("/vsbar/<int:ring>")
def vsbar_ring(ring):
    return vsbar_by_index(ring)


def vsbar_slugs(ring):
    """Tournaments a VS bar watches: its ring's, or for ring 1 with no rings set at all, every one not finished."""
    if RING_TOURNAMENTS or ring != 1:
        return RING_TOURNAMENTS.get(ring, [])
    # Finished tournaments can't start a fight, so once they're known to be complete they cost no more calls.
    return [slug for slug in tournament_ids if (peek_tournament(slug)[0] or {}).get("state") != "complete"]


def vsbar_by_index(ring):
    # Only load the tournaments this bar can show, so a VS bar never keeps other tournaments refreshing.
    # Unknown ring -> nothing watched -> blank screen (fail safe).
    underway = []
    for slug in vsbar_slugs(ring):
        try:
            info, bracket = load_tournament(slug)
        except Exception as e:
            print(f"Failed to load tournament {slug} for the VS bar: {e}")
            continue
        if not info:
            continue
        b = Bracket(bracket["matches"], bracket["participants"], info.get("tournament_type"))
        for m in bracket["matches"]:
            started = parse_time(m.get("underway_at"))
            if m.get("state") == "open" and started and m.get("player1_id") and m.get("player2_id"):
                underway.append((started, b.player(m["player1_id"])["name"], b.player(m["player2_id"])["name"]))
    if underway:
        # The fight marked underway most recently wins, so one left on by mistake doesn't hide the next.
        _, player1, player2 = max(underway, key=itemgetter(0))

        def trim(name):
            name = name or ""
            return name[:20] + "…" if len(name) > 20 else name
        match_data = {
            "player1": trim(player1),
            "versus": "&nbsp;&nbsp;&nbsp;",
            "player2": trim(player2),
        }
        return render_template("vsbar.html", match=match_data)
    else:
        match_data = {"player1": "", "versus": ".", "player2": ""}
        return render_template("vsbar.html", match=match_data)


def generate_json_from_matches_by_state(state_filter, slugs=None):
    """Matches across tournaments (all on the list, or just slugs), in play order."""
    all_relevant_matches = []
    match_start = datetime.now(timezone) + NEXT_MATCH_START
    json_data = []
    for tid in tournament_ids if slugs is None else slugs:
        try:
            tournament, bracket = load_tournament(tid)
            if not tournament:
                print(f"Tournament {tid} not found")
                continue

            # Copy each match so the tags added below don't touch the cached data.
            matches = [dict(m) for m in get_all_matches(bracket)]
            if not matches:
                print(f"No matches found for tournament {tid}")
                continue

            tournament_name = tournament.get("name", "Unknown Tournament")
            for m in matches:
                m["tournament"] = tournament_name
                m["tournament_slug"] = tid
            all_relevant_matches.extend(matches)

            if state_filter in ["pending", "open"]:
                all_relevant_matches = [
                    match
                    for match in all_relevant_matches
                    if match.get("state") == state_filter
                ]

        except Exception as e:
            print(f"Failed to load tournament {tid}: {e}")
            continue  # Skip to the next tournament if any error occurs

    interleaved_matches = interleave_matches(
        all_relevant_matches
    )  # Interleave matches here based on tournament name and suggested play order

    for match in interleaved_matches:
        if match["state"] not in ["open", "pending"]:
            continue

        match_data = {
            "MatchId": match["id"],
            "time": match_start.strftime("%I:%M%P %Z"),
            "player1": match["player1_name"],
            "player2": match["player2_name"],
            "tournament": match["tournament"],
            "tournament_slug": match.get("tournament_slug", ""),
            "status": match.get("state", "open"),
            "NxtMatch": match["next_player_match"],
            "NxtName": match["next_player_name"],
            "round": match["round"],
            "suggested_play_order": match["suggested_play_order"],
            "underway_at": match["underway_at"],
        }
        json_data.append(match_data)
        match_start += MATCH_DELAY

    return jsonify(json_data)


# Registered last and matched only after every named route, so /admin, /live, /vsbar... always win.
# Adding a tournament refuses IDs that would clash (reserved_slugs).
@app.route("/<slug>")
def bracket(slug):
    """OBS browser source for one tournament's bracket, at the same path Brackinator used."""
    t = next((t for t in tournaments if t["slug"] == slug), None)
    if t is None:
        abort(404)
    display = {**DEFAULT_DISPLAY, **t.get("display", {})}
    name = t.get("name") or slug
    try:
        info, bracket_data = load_tournament(slug)
        data = build_bracket_data(info.get("name") or name, slug, display, info,
                                  bracket_data["matches"], bracket_data["participants"])
    except Exception as e:
        print(f"Failed to build the bracket for {slug}: {e}")
        data = {"error": str(e), "tournament_name": name, "slug": slug, **display}
    return render_template("tournament.html", data=data)
