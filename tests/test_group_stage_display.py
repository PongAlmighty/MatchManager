"""Tests for group stage display backend logic (build_group_stage_data).

Covers:
1. raw_group_matches filtered correctly by group_id is not None
2. build_group_stage_data() returns correct keys and structure
3. filter_rounds_to_display() is called on group-only matches
4. Empty group matches edge case — no crash, empty results
"""
import sys
import os

# Add project root to path so we can import bracket_view
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bracket_view import (
    get_potential_round_numbers,
    filter_rounds_to_display,
    filter_placeholder_rounds,
    process_match_detailed,
    build_group_stage_data,
)


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def make_raw_match(match_id, round_num, player1_id, player2_id, state='pending', group_id=None):
    """Create a minimal raw Challonge match dict."""
    return {
        'id': match_id,
        'round': round_num,
        'player1_id': player1_id,
        'player2_id': player2_id,
        'state': state,
        'group_id': group_id,
        'winner_id': None,
        'loser_id': None,
        'underway_at': None,
        'player1_prereq_match_id': None,
        'player2_prereq_match_id': None,
    }


def make_participants_data():
    """Return a small participants_data lookup for test matches."""
    return {
        1: {'name': 'Alice', 'seed': 1, 'final_rank': None},
        2: {'name': 'Bob', 'seed': 2, 'final_rank': None},
        3: {'name': 'Carol', 'seed': 3, 'final_rank': None},
        4: {'name': 'Dave', 'seed': 4, 'final_rank': None},
    }


def make_tournament_stub():
    """Minimal tournament-like object with .name and .slug."""
    class T:
        name = 'Test Tournament'
        slug = 'test-slug'
    return T()


def make_display_settings():
    return {
        'background_color': '#b5d16c',
        'title_color': '#FFFFFF',
        'round_title_bg_color': '#2C2C2C',
    }


# ---------------------------------------------------------------------------
# Test 1: raw_group_matches filtered correctly
# ---------------------------------------------------------------------------

def test_group_id_filter_keeps_only_group_matches():
    """Test 1: Only matches with group_id is not None are included in raw_group_matches."""
    matches = [
        make_raw_match(1, 1, 1, 2, group_id=10),   # group match
        make_raw_match(2, 1, 3, 4, group_id=None),  # bracket match — NOT group
        make_raw_match(3, 2, 1, 3, group_id=10),   # group match
        make_raw_match(4, 2, 2, 4, group_id=None),  # bracket match — NOT group
    ]

    raw_group_matches = [m for m in matches if m.get('group_id') is not None]

    assert len(raw_group_matches) == 2, (
        f"Expected 2 group matches, got {len(raw_group_matches)}"
    )
    for m in raw_group_matches:
        assert m['group_id'] is not None, "Filtered match must have non-None group_id"
    # Non-group matches excluded
    non_group_ids = {m['id'] for m in raw_group_matches}
    assert 2 not in non_group_ids
    assert 4 not in non_group_ids


# ---------------------------------------------------------------------------
# Test 2: build_group_stage_data() returns correct structure
# ---------------------------------------------------------------------------

def test_build_group_stage_data_returns_correct_keys():
    """Test 2: build_group_stage_data() returns dict with required keys and correct types.

    The implementation uses group_rounds — a list of round dicts, each with:
    {round: int, columns: list of {round, group_label, matches, use_compact, is_placeholder}}.
    Rounds are sorted ascending; columns within each round use canonical group labels from round 1.
    """
    matches = [
        make_raw_match(1, 1, 1, 2, state='open', group_id=10),
        make_raw_match(2, 1, 3, 4, state='open', group_id=10),
        make_raw_match(3, 2, 1, 3, state='pending', group_id=10),
        make_raw_match(4, 2, 2, 4, state='pending', group_id=10),
    ]
    participants_data = make_participants_data()
    player_colors = {'Alice': '#ff0000', 'Bob': '#00ff00', 'Carol': '#0000ff', 'Dave': '#ffff00'}
    display_settings = make_display_settings()
    tournament = make_tournament_stub()

    result = build_group_stage_data(matches, participants_data, player_colors, display_settings, tournament.name, tournament.slug)

    # Required top-level keys
    assert 'group_rounds' in result, "Missing key: group_rounds"
    assert 'is_group_stage' in result, "Missing key: is_group_stage"
    assert 'error' in result, "Missing key: error"
    assert 'tournament_name' in result, "Missing key: tournament_name"
    assert 'slug' in result, "Missing key: slug"

    # Type checks
    assert isinstance(result['group_rounds'], list), "group_rounds must be a list"
    assert result['is_group_stage'] is True, "is_group_stage must be True"
    assert result['error'] is None, "error must be None"

    # Each round row must have required keys; columns within must have required keys
    round_nums = []
    for row in result['group_rounds']:
        assert 'round' in row, f"Round row missing 'round': {row}"
        assert 'columns' in row, f"Round row missing 'columns': {row}"
        round_nums.append(row['round'])
        for col in row['columns']:
            assert 'round' in col, f"Column missing 'round': {col}"
            assert 'group_label' in col, f"Column missing 'group_label': {col}"
            assert 'matches' in col, f"Column missing 'matches': {col}"
            assert 'use_compact' in col, f"Column missing 'use_compact': {col}"
            assert isinstance(col['round'], int), f"Column round must be int"
            assert isinstance(col['group_label'], str), f"Column group_label must be str"
            assert isinstance(col['matches'], list), f"Column matches must be list"

    # Rounds must be sorted ascending
    assert round_nums == sorted(round_nums), "group_rounds must be sorted ascending by round"


# ---------------------------------------------------------------------------
# Test 3: filter_rounds_to_display called on group-only matches
# ---------------------------------------------------------------------------

def test_filter_rounds_to_display_uses_group_matches_only():
    """Test 3: When only group matches provided, display rounds reflect those matches, not all matches.

    Scenario: tournament has both group matches (rounds 1-2 with group_id=10)
    and bracket matches (rounds 1-3 with group_id=None).
    build_group_stage_data should only see rounds from group matches.
    group_columns will only contain entries for group-match rounds (1 and/or 2), not round 3.
    """
    # Group matches: rounds 1 and 2, both open
    group_matches = [
        make_raw_match(1, 1, 1, 2, state='open', group_id=10),
        make_raw_match(2, 2, 3, 4, state='pending', group_id=10),
    ]
    # Bracket matches: rounds 1, 2, 3 (NOT group stage)
    bracket_matches = [
        make_raw_match(10, 1, 1, 2, state='open', group_id=None),
        make_raw_match(11, 2, 3, 4, state='open', group_id=None),
        make_raw_match(12, 3, 1, 3, state='pending', group_id=None),
    ]
    all_matches = group_matches + bracket_matches

    participants_data = make_participants_data()
    player_colors = {}
    display_settings = make_display_settings()
    tournament = make_tournament_stub()

    result = build_group_stage_data(all_matches, participants_data, player_colors, display_settings, tournament.name, tournament.slug)

    # The max round in group matches is 2; round 3 is only a bracket round
    # group_rounds must not contain any row with round=3
    row_rounds = [row['round'] for row in result['group_rounds']]
    assert 3 not in row_rounds, (
        "Round 3 is only a bracket round; it must not appear in group_rounds"
    )
    # All round rows must be from group matches (1 and/or 2)
    for r in row_rounds:
        assert r in [1, 2], f"Round {r} is not a group stage round"


# ---------------------------------------------------------------------------
# Test 4: Empty group matches — no crash, empty results
# ---------------------------------------------------------------------------

def test_build_group_stage_data_empty_matches():
    """Test 4: When no group matches exist, returns empty group_rounds list without crashing."""
    # All matches lack group_id — none are group stage
    matches = [
        make_raw_match(1, 1, 1, 2, group_id=None),
        make_raw_match(2, 2, 3, 4, group_id=None),
    ]
    participants_data = make_participants_data()
    player_colors = {}
    display_settings = make_display_settings()
    tournament = make_tournament_stub()

    result = build_group_stage_data(matches, participants_data, player_colors, display_settings, tournament.name, tournament.slug)

    assert result['group_rounds'] == [], (
        f"Expected empty list, got {result['group_rounds']}"
    )
    assert result['is_group_stage'] is True
    assert result['error'] is None
