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
tournament_ids = ["2024UCRSpringFlingBeetles", "2024UCRSpringFlingPlasticAnts"]
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
            prerequisite_matches.setdefault(m["player1_prereq_match_id"], []).append(m["id"])
        if m.get("player2_prereq_match_id"):
            prerequisite_matches.setdefault(m["player2_prereq_match_id"], []).append(m["id"])

    next_player_matches = {}
    for match in matches:
        match_id = match["id"]
        next_ids = prerequisite_matches.get(match_id)
        if next_ids:
            next_match = next((m for m in matches if m["id"] == next_ids[0]), None)
            if next_match:
                next_player_id = next_match.get("player1_id") or next_match.get("player2_id")
                next_player_name = participants_dict.get(next_player_id, None)
                currentMatchId = match_id
                nextMatchId = next_ids[0]
                nextPlayerName = next_player_name
                if match_id == 364002003:
                    print(f"Next player ID: {next_player_id}")  # Debugging output
                    print(f"Next player name: {nextPlayerName}")
                    
          
            relevant_matches = []
            for match in matches:
                match_id = match["id"]
                next_ids = prerequisite_matches.get(match_id)
          
                # Initialize default values for nextPlayerName and nextMatchId here
                nextPlayerName = None
                nextMatchId = None
          
                if next_ids:
                    next_match = next((m for m in matches if m["id"] == next_ids[0]), None)
                    if next_match:
                        next_player_id = next_match.get("player1_id") or next_match.get("player2_id")
                        nextPlayerName = "Next Opponent: " + participants_dict.get(next_player_id) if next_player_id else ""
                        nextMatchId = next_ids[0]
                        #if match_id == 364002003:
                            #print(f"Next player ID: {next_player_id}")  # Debugging output
                            #print(f"Next player name: {nextPlayerName}")
          
                # Now constructs relevant_matches with the guaranteed initialized variables
                if match["state"] != "complete" and (match.get("player1_id") or match.get("player2_id")):
                    relevant_matches.append({
                        "id": match["id"],
                        "tournament_id": match["tournament_id"],
                        "state": match["state"],
                        "player1_id": match["player1_id"],
                        "player1_name": participants_dict.get(match.get("player1_id"), None),
                        "player2_id": match["player2_id"],
                        "player2_name": participants_dict.get(match.get("player2_id"), None),
                        "player1_prereq_match_id": match["player1_prereq_match_id"],
                        "player2_prereq_match_id": match["player2_prereq_match_id"],
                        "created_at": match["created_at"],
                        "updated_at": match["updated_at"],
                        "round": match["round"],
                        "suggested_play_order": match["suggested_play_order"],
                        "next_player_match": nextMatchId,  
                        "next_player_name": nextPlayerName  
                    })
    return relevant_matches

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
    sorted_matches_list = [[] for _ in matches_list]
    for i, ml in enumerate(matches_list):
        sorted_matches_list[i] = sorted(ml, key=itemgetter("suggested_play_order"), 
                                        reverse=False)  
    interleaved_with_fill = zip_longest(*sorted_matches_list)
    list_of_tuples = chain.from_iterable(interleaved_with_fill)
    remove_fill = [x for x in list_of_tuples if x is not None]
    return remove_fill

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
        match for match in filtered_data.get_json()
        if match["NxtName"] != ""
    ]
    return render_template('filtered_matches.html', matches=filtered_matches)

@app.route('/filtered_matches.json')
def raw_filtered_matches():
    filtered_data = generate_json_from_matches_by_state("all")
    filtered_matches = [
        match for match in filtered_data.get_json()
        if match["NxtName"] != ""
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
    tournaments = {}
    match_start = datetime.now(timezone) + NEXT_MATCH_START
    json_data = []
    for tid in tournament_ids:
        try:
            tournament = challonge.tournaments.show(tid)
            tournaments[tournament["id"]] = tournament
            matches = get_all_matches(tid)
            if state_filter in ["pending", "open"]:
                matches = [match for match in matches if match["state"] == state_filter]
            participants = challonge.participants.index(tid)
            tournaments[tournament["id"]]["participants"] = {p["id"]: p for p in participants}
            tournaments[tournament["id"]]["matches"] = matches

            filtered_matches = matches if state_filter != "pending" else fetch_pending_matches(tournaments)

            for match in filtered_matches:
                if match["state"] not in ["open", "pending"]:
                    continue

                tournament_id = match["tournament_id"]
                tournament = tournaments.get(tournament_id, {})
                tournament_name = tournament.get("name", "Unknown Tournament")
                participants = tournament.get("participants", {})

                player1_name = participants.get(match.get('player1_id'), {}).get('name', None)
                player2_name = participants.get(match.get('player2_id'), {}).get('name', None)
                


                match_data = {
                    "MatchId": match["id"],
                    "time": match_start.strftime('%I:%M%P %Z'),
                    "player1": player1_name,
                    "player2": player2_name,
                    "tournament": tournament_name,
                    "status": match.get("state", "open"),
                    "NxtMatch": match["next_player_match"],
                    "NxtName": match["next_player_name"],
                    "round": match["round"],
                    "suggested_play_order": match["suggested_play_order"]
                }
                json_data.append(match_data)
                match_start += MATCH_DELAY
        except Exception as e:
            print(f"Failed to load tournament {tid}: {e}")
            return jsonify({"error": f"Failed to load tournament {tid}."})
    return jsonify(json_data)

if __name__ == "__main__":
    app.run(debug=True, port=8000)