import hmac
import json
import os
import re
import threading
from datetime import datetime, timedelta
from functools import wraps
from itertools import chain, zip_longest
from operator import itemgetter
from urllib.parse import urlparse

import challonge
import pytz
import requests
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

from story import SCREEN_MODES, build_screen_data, ready_match_options

app = Flask(__name__)
NEXT_MATCH_START = timedelta(minutes=1)
MATCH_DELAY = timedelta(minutes=3)
timezone = pytz.timezone("America/Los_Angeles")
UserName = os.environ["CHALLONGE_USERNAME"]
APIKey = os.environ["CHALLONGE_API_KEY"]
challonge.set_credentials(UserName, APIKey)

# Tournaments and their vsbar rings are managed from /admin and saved as a JSON file in
# Cloud Storage (CONFIG_BUCKET) so they survive restarts. Without CONFIG_BUCKET (local runs)
# they live in memory only and start from DEFAULT_TOURNAMENTS.
DEFAULT_TOURNAMENTS = [{"slug": "PongsTestTournament01", "ring": 1, "name": ""}]
CONFIG_BUCKET = os.environ.get("CONFIG_BUCKET", "")
CONFIG_OBJECT = "tournaments.json"
GCS_TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
tournaments = []  # [{"slug": ..., "ring": int or None, "name": ...}], in display order
tournament_ids = []
RING_TOURNAMENTS = {}  # vsbar ring number -> tournament slugs on that ring
config_error = ""
_config_lock = threading.Lock()


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


def set_tournaments(new, save=True):
    """Replace the tournament list. Saves first, so a failed save leaves things as they were."""
    global tournaments, tournament_ids, RING_TOURNAMENTS
    if save and CONFIG_BUCKET:
        _gcs_write(new)
    rings = {}
    for t in new:
        if t.get("ring"):
            rings.setdefault(t["ring"], []).append(t["slug"])
    tournaments, tournament_ids, RING_TOURNAMENTS = new, [t["slug"] for t in new], rings


def load_tournaments():
    global config_error
    data = None
    if CONFIG_BUCKET:
        try:
            data = _gcs_read()
        except Exception as e:
            config_error = f"Couldn't load saved tournaments ({e}). Using the defaults until you change the list."
            print(config_error)
    set_tournaments(data if data is not None else [dict(t) for t in DEFAULT_TOURNAMENTS], save=False)


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

# Every open overlay polls on its own timer; reuse Challonge responses for a few
# seconds so N overlays don't mean N times the API calls.
CACHE_TTL = timedelta(seconds=10)
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


class QueriesPaused(Exception):
    pass


def api(fn, *args):
    """Make one Challonge call and count it against the quota."""
    _state["api_calls"] += 1
    return fn(*args)


def pause(reason):
    _state["paused"] = True
    _state["paused_at"] = datetime.now(timezone)
    _state["pause_reason"] = reason
    print(f"Challonge queries paused: {reason}")


def resume():
    with _cache_lock:
        _state["paused"] = False
        _state["paused_at"] = None
        _state["pause_reason"] = ""
        # Start a fresh idle window so it doesn't immediately pause again.
        _state["last_change"] = datetime.now(timezone)
    print("Challonge queries resumed")


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


def fetch_bracket(tournament_id):
    """Every match and participant, as Challonge returns them. The overlays and story screens share this."""
    return {
        "matches": api(challonge.matches.index, tournament_id),
        "participants": api(challonge.participants.index, tournament_id),
    }


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


@app.route("/admin")
@require_admin
def admin():
    now = datetime.now(timezone)

    def fmt(t):
        return t.strftime("%b %d, %I:%M:%S %p %Z") if t else "never"

    idle_left = None
    if not _state["paused"]:
        idle_left = max(IDLE_PAUSE - (now - _state["last_change"]), timedelta(0))
    return render_template(
        "admin.html",
        state=_state,
        fmt=fmt,
        idle_left=str(idle_left).split(".")[0] if idle_left is not None else None,
        idle_pause=IDLE_PAUSE,
        tournaments=tournaments,
        screen_modes=SCREEN_MODES,
        screen_options={t["slug"]: screen_options(t["slug"]) for t in tournaments},
        msg=request.args.get("msg", ""),
        err=request.args.get("err") == "1",
        config_error=config_error,
        saved=bool(CONFIG_BUCKET),
    )


def back_to_admin(msg, err=False):
    return redirect(url_for("admin", msg=msg, err="1" if err else None))


def parse_ring(value):
    value = (value or "").strip()
    if not value:
        return None
    if not value.isdigit() or not 1 <= int(value) <= 99:
        raise ValueError("Ring must be a number from 1 to 99, or blank for none.")
    return int(value)


@app.route("/admin/tournaments/add", methods=["POST"])
@require_admin
def admin_add_tournament():
    global config_error
    slug = parse_tournament_id(request.form.get("tournament", ""))
    if not slug:
        return back_to_admin("Enter a Challonge tournament ID or URL.", err=True)
    try:
        ring = parse_ring(request.form.get("ring"))
    except ValueError as e:
        return back_to_admin(str(e), err=True)
    if slug in tournament_ids:
        return back_to_admin(f"{slug} is already on the list.", err=True)
    # One Challonge call to catch typos and get the tournament's name.
    try:
        info = api(challonge.tournaments.show, slug)
    except Exception as e:
        return back_to_admin(f"Challonge couldn't find {slug}: {e}", err=True)
    with _config_lock:
        try:
            set_tournaments(tournaments + [{"slug": slug, "ring": ring, "name": info.get("name", "")}])
        except Exception as e:
            return back_to_admin(f"Couldn't save the tournament list: {e}", err=True)
        config_error = ""
    return back_to_admin(f"Added {info.get('name') or slug}.")


@app.route("/admin/tournaments/ring", methods=["POST"])
@require_admin
def admin_set_ring():
    slug = request.form.get("slug", "")
    try:
        ring = parse_ring(request.form.get("ring"))
    except ValueError as e:
        return back_to_admin(str(e), err=True)
    with _config_lock:
        new = [dict(t, ring=ring) if t["slug"] == slug else t for t in tournaments]
        try:
            set_tournaments(new)
        except Exception as e:
            return back_to_admin(f"Couldn't save the tournament list: {e}", err=True)
    return back_to_admin(f"{slug} is now on {'ring ' + str(ring) if ring else 'no ring'}.")


@app.route("/admin/tournaments/remove", methods=["POST"])
@require_admin
def admin_remove_tournament():
    slug = request.form.get("slug", "")
    with _config_lock:
        try:
            set_tournaments([t for t in tournaments if t["slug"] != slug])
        except Exception as e:
            return back_to_admin(f"Couldn't save the tournament list: {e}", err=True)
    with _cache_lock:
        _cache.pop(("tournament", slug), None)
        _cache.pop(("bracket", slug), None)
    return back_to_admin(f"Removed {slug}.")


@app.route("/admin/resume", methods=["POST"])
@require_admin
def admin_resume():
    resume()
    return redirect(url_for("admin"))


@app.route("/admin/pause", methods=["POST"])
@require_admin
def admin_pause():
    with _cache_lock:
        pause("Paused from the admin page")
    return redirect(url_for("admin"))


@app.route("/admin/tournaments/screen", methods=["POST"])
@require_admin
def admin_set_screen():
    slug = request.form.get("slug", "")
    mode = request.form.get("screen_mode", "overview")
    if mode not in SCREEN_MODES:
        return back_to_admin(f"Unknown story screen {mode}.", err=True)
    pins = {}
    for key in ("pinned_upset", "pinned_elimination"):
        value = request.form.get(key, "").strip()
        if value and not value.isdigit():
            return back_to_admin("Pick a match from the list.", err=True)
        pins[key] = int(value) if value else None
    with _config_lock:
        new = [dict(t, screen_mode=mode, **pins) if t["slug"] == slug else t for t in tournaments]
        try:
            set_tournaments(new)
        except Exception as e:
            return back_to_admin(f"Couldn't save the tournament list: {e}", err=True)
    return back_to_admin(f"{slug} story screen: {mode}.")


def screen_options(slug):
    """Pin choices for the admin page, from cached data only, so the admin page never spends quota."""
    info, bracket = peek(("tournament", slug)), peek(("bracket", slug))
    if not info or not bracket:
        return None
    try:
        return ready_match_options(bracket["matches"], bracket["participants"], info.get("tournament_type"))
    except Exception as e:
        print(f"Couldn't list story screen matches for {slug}: {e}")
        return None


def screen_tournament(slug):
    """The tournament config for a story screen; only tournaments on the admin list can be shown."""
    t = next((t for t in tournaments if t["slug"] == slug), None)
    if t is None:
        abort(404)
    return t


@app.route("/screen/<slug>")
@app.route("/screen/<slug>/<mode>")
def story_screen(slug, mode=None):
    """OBS browser source for the story screens. Without a mode it follows the admin page."""
    t = screen_tournament(slug)
    if mode is not None and mode not in SCREEN_MODES:
        abort(404)
    data_url = url_for("story_screen_data", slug=slug, mode=mode)
    return render_template("story_screen.html", tournament_name=t.get("name") or slug,
                           background="#00ff00", data_url=data_url, refresh_ms=30000)


@app.route("/screen/<slug>/data")
def story_screen_data(slug):
    t = screen_tournament(slug)
    mode = request.args.get("mode") or t.get("screen_mode") or "overview"
    if mode not in SCREEN_MODES:
        mode = "overview"
    pinned = t.get("pinned_upset") if mode == "upset" else t.get("pinned_elimination")
    try:
        info = cached(("tournament", slug), lambda: api(challonge.tournaments.show, slug))
        bracket = cached(("bracket", slug), lambda: fetch_bracket(slug))
        return jsonify(build_screen_data(info.get("name") or t.get("name") or slug, bracket["matches"],
                                         bracket["participants"], info.get("tournament_type"), mode,
                                         pinned_match_id=pinned))
    except Exception as e:
        print(f"Failed to build story screen for {slug}: {e}")
        return jsonify({"error": str(e)}), 503


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


@app.route("/vsbar")
def vsbar():
    return vsbar_by_index(1)


@app.route("/vsbar/<int:ring>")
def vsbar_ring(ring):
    return vsbar_by_index(ring)


def vsbar_by_index(ring):
    matches_data = generate_json_from_matches_by_state("open")
    matches = matches_data.get_json()
    if ring is None:
        # /vsbar: first underway match across ALL tournaments
        underway = [match for match in matches if match.get("underway_at")]
    else:
        # /vsbar/<ring>: only this ring's tournaments.
        # Unknown ring -> empty list -> blank screen (fail safe).
        ring_tournament_ids = RING_TOURNAMENTS.get(ring, [])
        underway = [
            match for match in matches
            if match.get("underway_at") and match.get("tournament_slug") in ring_tournament_ids
        ]
    selected_match = underway[0] if underway else None
    if selected_match:
        def trim(name):
            name = name or ""
            return name[:20] + "…" if len(name) > 20 else name
        match_data = {
            "player1": trim(selected_match["player1"]),
            "versus": "&nbsp;&nbsp;&nbsp;",
            "player2": trim(selected_match["player2"]),
        }
        return render_template("vsbar.html", match=match_data)
    else:
        match_data = {"player1": "", "versus": ".", "player2": ""}
        return render_template("vsbar.html", match=match_data)


def generate_json_from_matches_by_state(state_filter):
    all_relevant_matches = []
    match_start = datetime.now(timezone) + NEXT_MATCH_START
    json_data = []
    for tid in tournament_ids:
        try:
            tournament = cached(("tournament", tid), lambda: api(challonge.tournaments.show, tid))
            if not tournament:
                print(f"Tournament {tid} not found")
                continue

            # Copy each match so the tags added below don't touch the cached data.
            bracket = cached(("bracket", tid), lambda: fetch_bracket(tid))
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
