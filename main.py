# Standard library imports
from datetime import datetime, timedelta
from itertools import chain, zip_longest
from operator import itemgetter
import os
import sys
import pytz
from wsgiref.simple_server import make_server
# Related third-party imports
import challonge
import time


NEXT_MATCH_START = timedelta(minutes=1)
MATCH_DELAY = timedelta(minutes=3)
timezone = pytz.timezone('America/Los_Angeles')

def most_recent_match_time(tournament):
    most_recent_match_time = datetime.min
    for m in tournament["matches"]:
        if m["state"] != "complete":
            continue
        match_time = m["updated_at"].replace(tzinfo=timezone)
        if match_time > most_recent_match_time:
            most_recent_match_time = match_time
    return most_recent_match_time

def interleave_matches(tournaments):
    matches_list = [t["matches"] for t in sorted(tournaments.values(), key=most_recent_match_time)]
    for i, ml in enumerate(matches_list):
        matches_list[i] = sorted([m for m in ml if m["state"] == "open"], key=itemgetter("suggested_play_order"))
    interleaved_with_fill = zip_longest(*matches_list)
    list_of_tuples = chain.from_iterable(interleaved_with_fill)
    remove_fill = [x for x in list_of_tuples if x is not None]
    return remove_fill

def create_html_from_output(output):
    html = []
    html.append('<!DOCTYPE html>')
    html.append('<html>')
    html.append('<head><title>Current Matches</title>')
    html.append('<link rel="stylesheet" type="text/css" href="styles.css">')
    html.append('<meta http-equiv="refresh" content="20">')  # Auto-refresh every 20 seconds.
    html.append('</head>')
    html.append('<body class="page-body">')
    html.append('<h1 class="title">Current Matches</h1>')
    for line in output:
        time, rest = line.split(" - ", 1)
        player_names, tournament_name = rest.rsplit(" (", 1)
        tournament_name = tournament_name.rstrip(")")
        html.append(f'<p><span class="time">{time}</span> - <span class="players">{player_names}</span> <span class="tournament">({tournament_name})</span></p>')
    html.append('</body>')
    html.append('</html>')
    return "\n".join(html)

def application(environ, start_response):
  if environ['PATH_INFO'] == '/styles.css':
    status = '200 OK'
    headers = [('Content-type', 'text/css')]
    start_response(status, headers)
    with open('styles.css', 'rb') as f:  # Ensure this path is correct
        return [f.read()]
  status = '200 OK'
  headers = [('Content-type', 'text/html')]
  start_response(status, headers)


  
  # Main content generation logic from the original main() packed into a single function
  UserName = os.environ['CHALLONGE_USERNAME']
  APIKey = os.environ['CHALLONGE_API_KEY']
  challonge.set_credentials(UserName, APIKey)
  tournament_ids = ["SonoranShowdownBeetleweight"]
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
          matches[y]["player1_name"] = participants.get(match["player1_id"], {}).get("name", "???")
          matches[y]["player2_name"] = participants.get(match["player2_id"], {}).get("name", "???")
      tournaments[t]["matches"] = matches
  ordered_matches = interleave_matches(tournaments)
  match_start = datetime.now(timezone) + NEXT_MATCH_START
  output = []
  for i, match in enumerate(ordered_matches[:10]):
      tournament_name = tournaments.get(match["tournament_id"], {}).get("name")
      output_line = f"{match_start.strftime('%I:%M %p')} - {match['player1_name']} vs {match['player2_name']} ({tournament_name})"
      output.append(output_line)
      match_start += MATCH_DELAY
  html = create_html_from_output(output)

  return [html.encode("utf-8")]

if __name__ == "__main__":
    httpd = make_server('', 8000, application)
    print(f"Serving on port 8000...")
    httpd.serve_forever()