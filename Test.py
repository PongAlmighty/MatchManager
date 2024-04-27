import os
import challonge
import json
from datetime import datetime

# Setup Challonge credentials
CHALLONGE_USERNAME = os.environ['CHALLONGE_USERNAME']  # Ensure this environment variable is set
CHALLONGE_API_KEY = os.environ['CHALLONGE_API_KEY']
challonge.set_credentials(CHALLONGE_USERNAME, CHALLONGE_API_KEY)

# Define a variable for the API test you want to conduct
api_test_to_perform = "list_matches"  # Can be 'tournament_details', 'list_tournaments', 'list_matches', etc.

def custom_json_serializer(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError("Type %s not serializable" % type(obj))

def get_tournament_details(tournament_id):
    """Fetch tournament details."""
    try:
        return challonge.tournaments.show(tournament_id)
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def list_tournaments():
    """List all tournaments."""
    try:
        return challonge.tournaments.index()
    except Exception as e:
        print(f"An error occurred: {e}")
        return []

def list_matches(tournament_id):
    """List all matches for a given tournament."""
    try:
        return challonge.matches.index(tournament_id)
    except Exception as e:
        print(f"An error occurred: {e}")
        return []

def write_data_to_json_file(data, filename):
    """Write data to a JSON file with pretty formatting, handling datetime serialization."""
    with open(filename, 'w') as file:
        json.dump(data, file, indent=4, default=custom_json_serializer)

if __name__ == "__main__":
    tournament_id = "2024UCRSpringFlingPlasticAnts"  # Change this to your specific tournament ID
    data = None

    if api_test_to_perform == "tournament_details":
        data = get_tournament_details(tournament_id)
    elif api_test_to_perform == "list_tournaments":
        data = list_tournaments()
    elif api_test_to_perform == "list_matches":
        data = list_matches(tournament_id)

    if data is not None:
        filename = f"{api_test_to_perform}.json"
        write_data_to_json_file(data, filename)
        print(f"Data saved to {filename}")
    else:
        print("No data to save.")