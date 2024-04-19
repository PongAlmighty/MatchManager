# Standard library imports
import json
import os
from datetime import datetime, timedelta
from itertools import chain, zip_longest
from operator import itemgetter
from wsgiref.simple_server import make_server

# Related third-party imports
import challonge
import pytz

NEXT_MATCH_START = timedelta(minutes=1)
MATCH_DELAY = timedelta(minutes=3)
timezone = pytz.timezone('America/Los_Angeles')


def most_recent_match_time(tournament):
  # Adjusting to use the defined timezone instead of UTC directly
  most_recent_match_time = datetime.min.replace(
      tzinfo=timezone)  # Now using 'America/Los_Angeles'
  for m in tournament["matches"]:
    if m["state"] != "complete":
      continue
    match_time = m["updated_at"]
    # Adjust the following to handle timezone correctly
    if isinstance(match_time,
                  datetime):  # Checking if match_time is a datetime object
      match_time = match_time.replace(
          tzinfo=timezone)  # Using 'America/Los_Angeles' instead of UTC
    if match_time > most_recent_match_time:
      most_recent_match_time = match_time
  return most_recent_match_time


def interleave_matches(tournaments):
  matches_list = [
      t["matches"]
      for t in sorted(tournaments.values(), key=most_recent_match_time)
  ]
  for i, ml in enumerate(matches_list):
    matches_list[i] = sorted([m for m in ml if m["state"] == "open"],
                             key=itemgetter("suggested_play_order"))
  interleaved_with_fill = zip_longest(*matches_list)
  list_of_tuples = chain.from_iterable(interleaved_with_fill)
  remove_fill = [x for x in list_of_tuples if x is not None]
  return remove_fill[:6]


def get_pending_matches(tournaments):
  matches_list = [
      t["matches"]
      for t in sorted(tournaments.values(), key=most_recent_match_time)
  ]
  for i, ml in enumerate(matches_list):
    matches_list[i] = sorted([m for m in ml if m["state"] == "pending"],
                             key=itemgetter("suggested_play_order"))
  interleaved_with_fill = zip_longest(*matches_list)
  list_of_tuples = chain.from_iterable(interleaved_with_fill)
  remove_fill = [x for x in list_of_tuples if x is not None]
  return remove_fill


def create_html_from_output(output):
  with open('current_matches.html', 'r') as f:  # Ensure this path is correct

    current_matches_html = f.read()
    return current_matches_html


def application(environ, start_response):
  path = environ['PATH_INFO']
  print(path)
  if path == '/styles.css':
    status = '200 OK'
    headers = [('Content-type', 'text/css')]
    start_response(status, headers)
    with open('styles.css', 'rb') as f:
      return [f.read()]
  else:
    UserName = os.environ['CHALLONGE_USERNAME']
    APIKey = os.environ['CHALLONGE_API_KEY']
    challonge.set_credentials(UserName, APIKey)
    tournament_ids = [
        "SonoranShowdownBeetleweight", "2024UCRSpringFlingBeetles"
    ]
    tournaments = {}
    for tid in tournament_ids:
      try:
        tournament = challonge.tournaments.show(tid)
        tournaments[tournament["id"]] = tournament
      except Exception as e:
        return [f"Failed to load tournament {tid}: {e}".encode('utf-8')]
    if not tournaments:
      return [b"No tournaments found with the provided IDs."]
    for t in tournaments:
      matches = challonge.matches.index(t, state="all")
      participants = challonge.participants.index(t)
      participants = {p["id"]: p for p in participants}
      for y, match in enumerate(matches):
        matches[y]["player1_name"] = participants.get(match["player1_id"],
                                                      {}).get("name", "???")
        matches[y]["player2_name"] = participants.get(match["player2_id"],
                                                      {}).get("name", "???")
      tournaments[t]["matches"] = matches
    ordered_matches = interleave_matches(tournaments)

    if path == '/matches_data.json':
      status = '200 OK'
      headers = [('Content-type', 'application/json')]
      start_response(status, headers)
      match_data = generate_matches_data_for_json(tournaments)
      return [json.dumps(match_data).encode("utf-8")]
    else:
      match_start = datetime.now(timezone) + NEXT_MATCH_START
      output = []
      for i, match in enumerate(ordered_matches[:10]):
        tournament_name = tournaments.get(match["tournament_id"],
                                          {}).get("name")
        output_line = f"{match_start.strftime('%I:%M %p')} - {match['player1_name']} vs {match['player2_name']} ({tournament_name})"
        output.append(output_line)
        match_start += MATCH_DELAY
      html = create_html_from_output(output)
      status = '200 OK'
      headers = [('Content-type', 'text/html')]
      start_response(status, headers)
      return [html.encode("utf-8")]


def generate_matches_data_for_json(tournaments):
  NxtMatchID = None
  NxtOpponent = None
  NxtMatch = None
  ordered_matches = interleave_matches(tournaments)
  future_matches = get_pending_matches(tournaments)
  match_start = datetime.now(timezone) + NEXT_MATCH_START
  json_data = []
  for match in ordered_matches:
    if match["state"] == "open":
      tournament_name = tournaments.get(match["tournament_id"], {}).get("name")
      player1_name = match.get('player1_name', 'Unknown')
      player2_name = match.get('player2_name', 'Unknown')

      for NxtMatch in future_matches:
        if NxtMatch["state"] == "pending" and \
           ((match["id"] == NxtMatch.get("player1_prereq_match_id") and NxtMatch.get("player2_prereq_match_id") is None) or \
            (match["id"] == NxtMatch.get("player2_prereq_match_id") and NxtMatch.get("player1_prereq_match_id") is None)):
          NxtMatchID = NxtMatch.get("id")
          break
      else:
        NxtMatchID = None

      if NxtMatchID is not None:
        NxtOpponent = None
        NxtOpponent = NxtMatch.get("player1_name")
        if NxtOpponent is None:
          NxtOpponent = NxtMatch.get("player2_name")
      NxtOpponent = '' if NxtOpponent is None else 'Winner Fights: ' + NxtOpponent

      match_data = {
          "UpcmgMatchId": match.get("id"),
          "time": match_start.strftime(
              '%I:%M%P %Z'
          ),  # Now should display time in 'America/Los_Angeles'
          "player1": player1_name,
          "player2": player2_name,
          "tournament": tournament_name,
          "status": match.get("state", "open"),
          "NextMatch": NxtMatchID,
          "NextOpp": NxtOpponent
      }

      json_data.append(match_data)
      NxtMatchID = None
      NxtOpponent = None
      NxtMatch = None
      match_start += MATCH_DELAY  # Increment start time for each match
  return json_data


if __name__ == "__main__":
  httpd = make_server('', 8000, application)
  print(f"Serving on port 8000...")
  httpd.serve_forever()
