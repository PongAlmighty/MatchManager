import json
import os
from datetime import datetime, timedelta
from itertools import chain, zip_longest
from operator import itemgetter

import challonge
import pytz
from flask import Flask, jsonify, render_template

app = Flask(__name__)
NEXT_MATCH_START = timedelta(minutes=1)
MATCH_DELAY = timedelta(minutes=3)
timezone = pytz.timezone('America/Los_Angeles')
tournament_ids = ["PongsTestTournament01"]
UserName = os.environ['CHALLONGE_USERNAME']
APIKey = os.environ['CHALLONGE_API_KEY']
challonge.set_credentials(UserName, APIKey)


def get_all_matches(tournament_id):
    matches = challonge.matches.index(tournament_id)
    participants = challonge.participants.index(tournament_id)
    participants_dict = {p["id"]: p["name"] for p in participants}

    prerequisite_matches = {}
    for m in matches:
        if m.get("player1_prereq_match_id"):
            prerequisite_matches.setdefault(m["player1_prereq_match_id"],
                                            []).append(m["id"])
        if m.get("player2_prereq_match_id"):
            prerequisite_matches.setdefault(m["player2_prereq_match_id"],
                                            []).append(m["id"])

    relevant_matches = []
    for match in matches:
        match_id = match["id"]
        next_ids = prerequisite_matches.get(match_id)
        nextPlayerName = None
        nextMatchId = None

        if next_ids:
            next_match = next((m for m in matches if m["id"] == next_ids[0]),
                              None)
            if next_match:
                next_player_id = next_match.get(
                    "player1_id") or next_match.get("player2_id")
                if next_player_id:
                    nextPlayerName = "Next Opponent: " + participants_dict.get(next_player_id, "")
                else:
                    nextPlayerName = ""
                nextMatchId = next_ids[0]

        if match["state"] != "complete" and (match.get("player1_id")
                                             or match.get("player2_id")):
            relevant_matches.append({
                "id":
                match["id"],
                "tournament_id":
                match["tournament_id"],
                "state":
                match["state"],
                "player1_id":
                match["player1_id"],
                "player1_name":
                participants_dict.get(match.get("player1_id"), None),
                "player2_id":
                match["player2_id"],
                "player2_name":
                participants_dict.get(match.get("player2_id"), None),
                "player1_prereq_match_id":
                match["player1_prereq_match_id"],
                "player2_prereq_match_id":
                match["player2_prereq_match_id"],
                "created_at":
                match["created_at"],
                "updated_at":
                match["updated_at"],
                "round":
                match["round"],
                "suggested_play_order":
                match["suggested_play_order"],
                "next_player_match":
                nextMatchId,
                "next_player_name":
                nextPlayerName
            })
    return relevant_matches


def interleave_matches(matches):
    # Group matches by tournament_id
    grouped_matches = {}
    for match in matches:
        grouped_matches.setdefault(match['tournament_id'], []).append(match)

    # Sort each group by suggested_play_order
    for tournament_id in grouped_matches:
        grouped_matches[tournament_id].sort(
            key=lambda x: x['suggested_play_order'])

    # Interleave matches from each tournament in round-robin style
    interleaved_matches = []
    while any(grouped_matches.values()):
        for tournament_id in list(
                grouped_matches):  # Use list to avoid RuntimeError
            if grouped_matches[tournament_id]:
                interleaved_matches.append(
                    grouped_matches[tournament_id].pop(0))
            else:
                del grouped_matches[tournament_id]

    return interleaved_matches


def most_recent_match_time(tournament):
    most_recent_match_time = datetime.min.replace(tzinfo=timezone)
    for m in tournament["matches"]:
        if m["state"] != "complete":
            continue
        match_time = m["updated_at"]
        if isinstance(match_time, datetime):
            match_time = match_time.replace(tzinfo=timezone)
        if match_time > most_recent_match_time:
            most_recent_match_time = match_time
    return most_recent_match_time


def fetch_pending_matches(tournaments):
    pending_matches = []
    for tournament in tournaments.values():
        for match in tournament.get("matches", []):
            if match["state"] == "pending":
                pending_matches.append(match)
    return pending_matches


@app.route('/')
@app.route('/current_matches')
def index():
    return render_template('current_matches.html')


@app.route('/filtered_matches')
def filtered_matches():
    filtered_data = generate_json_from_matches_by_state("all")
    filtered_matches = [
        match for match in filtered_data.get_json() if match["NxtName"] != ""
    ]
    return render_template('filtered_matches.html', matches=filtered_matches)


@app.route('/filtered_matches.json')
def raw_filtered_matches():
    filtered_data = generate_json_from_matches_by_state("all")
    filtered_matches = [
        match for match in filtered_data.get_json() if match["NxtName"] != ""
    ]
    return jsonify(filtered_matches)


@app.route('/styles.css')
def static_file():
    return app.send_static_file('styles.css')


@app.route('/matches_data.json')
def matches_data():
    return generate_json_from_matches_by_state("all")


@app.route('/pending_matches.json')
def pending_matches():
    return generate_json_from_matches_by_state("pending")


@app.route('/open_matches.json')
def open_matches():
    return generate_json_from_matches_by_state("open")


def generate_json_from_matches_by_state(state_filter):
    all_relevant_matches = []
    match_start = datetime.now(timezone) + NEXT_MATCH_START
    json_data = []
    for tid in tournament_ids:
        try:
            tournament = challonge.tournaments.show(tid)
            matches = get_all_matches(tid)
            for m in matches:
                m['tournament'] = tournament.get('name','Unknown Tournament')  # Add tournament name to match
            all_relevant_matches.extend(
                matches)  # Collecting matches from all tournaments

            if state_filter in ["pending", "open"]:
                all_relevant_matches = [
                    match for match in all_relevant_matches
                    if match["state"] == state_filter
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
            "time": match_start.strftime('%I:%M%P %Z'),
            "player1": match["player1_name"],
            "player2": match["player2_name"],
            "tournament": match["tournament"],
            "status": match.get("state", "open"),
            "NxtMatch": match["next_player_match"],
            "NxtName": match["next_player_name"],
            "round": match["round"],
            "suggested_play_order": match["suggested_play_order"]
        }
        json_data.append(match_data)
        match_start += MATCH_DELAY

    return jsonify(json_data)
