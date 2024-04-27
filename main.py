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
tournament_ids = ["2024UCRSpringFlingBeetles", "2024UCRSpringFlingPlasticAnts"]
UserName = os.environ['CHALLONGE_USERNAME']
APIKey = os.environ['CHALLONGE_API_KEY']
challonge.set_credentials(UserName, APIKey)

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

def interleave_matches(tournaments):
    matches_list = [t["matches"] for t in sorted(tournaments.values(), key=most_recent_match_time)]
    sorted_matches_list = [None] * len(matches_list)
    for i, ml in enumerate(matches_list):
        sorted_matches_list[i] = sorted([m for m in ml if m["state"] == "open"], key=itemgetter("suggested_play_order"))
    interleaved_with_fill = zip_longest(*sorted_matches_list)
    list_of_tuples = chain.from_iterable(interleaved_with_fill)
    remove_fill = [x for x in list_of_tuples if x is not None]
    return remove_fill[:]

def get_pending_matches(tournaments):
    matches_list = [t["matches"] for t in sorted(tournaments.values(), key=most_recent_match_time)]
    sorted_matches_list = [None] * len(matches_list)
    for i, ml in enumerate(matches_list):
        sorted_matches_list[i] = sorted([m for m in ml if m["state"] == "pending"], key=itemgetter("suggested_play_order"))
    interleaved_with_fill = zip_longest(*sorted_matches_list)
    list_of_tuples = chain.from_iterable(interleaved_with_fill)
    remove_fill = [x for x in list_of_tuples if x is not None]
    return remove_fill

@app.route('/')
@app.route('/current_matches')
def index():
    return render_template('current_matches.html')

@app.route('/filtered_matches')
def filtered_matches():
    return render_template('filtered_matches.html')

@app.route('/styles.css')
def static_file():
    return app.send_static_file('styles.css')

@app.route('/matches_data.json')
def matches_data():
    tournaments = {}
    for tid in tournament_ids:
        try:
            tournament = challonge.tournaments.show(tid)
            tournaments[tournament["id"]] = tournament
            tournaments[tournament["id"]]["matches"] = challonge.matches.index(tid, state="all")
            participants = challonge.participants.index(tid)
            tournaments[tournament["id"]]["participants"] = {p["id"]: p for p in participants}
        except Exception as e:
            print(f"Failed to load tournament {tid}: {e}")
            return jsonify({"error": f"Failed to load tournament {tid}."})
    ordered_matches = interleave_matches(tournaments)
    future_matches = get_pending_matches(tournaments)
    json_data = generate_matches_data_for_json(tournaments, ordered_matches, future_matches)
    return jsonify(json_data)

def generate_matches_data_for_json(tournaments, ordered_matches, future_matches):
    match_start = datetime.now(timezone) + NEXT_MATCH_START
    json_data = []

    def find_next_match_for_current(match, future):
        for nxt in future:
            if nxt["state"] == "pending":
                match_id_conditions = (
                    match["id"] == nxt.get("player1_prereq_match_id") and nxt.get("player2_prereq_match_id") is None,
                    match["id"] == nxt.get("player2_prereq_match_id") and nxt.get("player1_prereq_match_id") is None,
                )
                if any(match_id_conditions):
                    return nxt
        return None

    for match in ordered_matches:
        if match["state"] != "open":
            continue

        tournament_id = match["tournament_id"]
        tournament = tournaments.get(tournament_id, {})
        tournament_name = tournament.get("name", "Unknown Tournament")
        participants = tournament.get("participants", {})

        player1_name = participants.get(match.get('player1_id'), {}).get('name', 'Unknown')
        player2_name = participants.get(match.get('player2_id'), {}).get('name', 'Unknown')
        NxtMatch = find_next_match_for_current(match, future_matches)

        NxtMatchID = NxtMatch.get("id", None) if NxtMatch else None
        NxtOpponent = ''
        if NxtMatchID:
            opponent_id = NxtMatch.get("player1_id") if match["id"] == NxtMatch.get("player2_prereq_match_id") else NxtMatch.get("player2_id")
            if opponent_id:
                NxtOpponent = 'Winner Fights: ' + participants.get(opponent_id, {}).get('name', 'Unknown')

        match_data = {
            "UpcmgMatchId": match.get("id"),
            "time": match_start.strftime('%I:%M%P %Z'),
            "player1": player1_name,
            "player2": player2_name,
            "tournament": tournament_name,
            "status": match.get("state", "open"),
            "NextMatch": NxtMatchID,
            "NextOpp": NxtOpponent
        }
        json_data.append(match_data)
        match_start += MATCH_DELAY
    return json_data


if __name__ == "__main__":
  app.run(debug=True, port=8000)