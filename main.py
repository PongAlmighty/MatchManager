import hmac
import json
import os
import threading
from datetime import datetime, timedelta
from functools import wraps
from itertools import chain, zip_longest
from operator import itemgetter

import challonge
import pytz
from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

app = Flask(__name__)
NEXT_MATCH_START = timedelta(minutes=1)
MATCH_DELAY = timedelta(minutes=3)
timezone = pytz.timezone("America/Los_Angeles")
tournament_ids = [ "PongsTestTournament01"]

# Map each vsbar ring number to the tournament IDs that run on it.
# Update this when the tournament list changes.
RING_TOURNAMENTS = {
    1: ["PongsTestTournament01"],
}
UserName = os.environ["CHALLONGE_USERNAME"]
APIKey = os.environ["CHALLONGE_API_KEY"]
challonge.set_credentials(UserName, APIKey)

# Warn at startup if any ring is pointed at a tournament that isn't being fetched.
for _ring, _slugs in RING_TOURNAMENTS.items():
    for _slug in _slugs:
        if _slug not in tournament_ids:
            print(f"WARNING: Ring {_ring} references '{_slug}', which is not in tournament_ids. That ring will never show its matches.")

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


def get_all_matches(tournament_id):
    matches = api(challonge.matches.index, tournament_id)
    participants = api(challonge.participants.index, tournament_id)
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
        tournaments=tournament_ids,
    )


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
            matches = [dict(m) for m in cached(("matches", tid), lambda: get_all_matches(tid))]
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
