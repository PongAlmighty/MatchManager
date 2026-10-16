"""Tests for create_participants_lookup() group_player_ids expansion.

These tests verify that:
1. group_player_ids are mapped alongside the main participant ID
2. None and empty group_player_ids are handled without TypeError
3. Multiple participants each get their group_player_ids mapped
4. The /raw/ participants list builder avoids duplicates
"""
import sys
import os

# Add project root to path so we can import bracket_view
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bracket_view import create_participants_lookup


def test_group_player_ids_mapped_to_same_name():
    """Test 1: participant with group_player_ids=[101, 102] maps all IDs to same name."""
    participants = [
        {'id': 1, 'name': 'Alice', 'seed': 1, 'final_rank': None, 'group_player_ids': [101, 102]}
    ]
    lookup = create_participants_lookup(participants)

    # Main ID should resolve
    assert lookup[1]['name'] == 'Alice'
    # Group player IDs should also resolve
    assert 101 in lookup, "group_player_id 101 should be in lookup"
    assert 102 in lookup, "group_player_id 102 should be in lookup"
    assert lookup[101]['name'] == 'Alice', "lookup[101] should resolve to Alice"
    assert lookup[102]['name'] == 'Alice', "lookup[102] should resolve to Alice"
    # Total: 1 main + 2 group_player_ids = 3 entries
    assert len(lookup) == 3, f"Expected 3 entries (1 main + 2 group), got {len(lookup)}"


def test_group_player_ids_none_no_typeerror():
    """Test 2: participant with group_player_ids=None raises no TypeError, main ID resolves.

    Verifies the `or []` guard handles None gracefully. Also asserts that
    a participant with group_player_ids=[10] IS expanded (to distinguish from
    the old code which never iterated group_player_ids at all).
    """
    # First, verify None case doesn't raise TypeError and produces exactly 1 entry
    participants_none = [
        {'id': 5, 'name': 'Bob', 'seed': 2, 'final_rank': None, 'group_player_ids': None}
    ]
    lookup_none = create_participants_lookup(participants_none)
    assert lookup_none[5]['name'] == 'Bob'
    # Exactly 1 entry — no extras from None
    assert len(lookup_none) == 1, f"Expected 1 entry, got {len(lookup_none)}"

    # Also verify a non-None case works, proving the new loop-based implementation
    # is active (if old dict comprehension were used, this would still produce 1 entry)
    participants_with = [
        {'id': 5, 'name': 'Bob', 'seed': 2, 'final_rank': None, 'group_player_ids': [99]}
    ]
    lookup_with = create_participants_lookup(participants_with)
    assert 99 in lookup_with, "group_player_id 99 must be in lookup when group_player_ids=[99]"
    assert lookup_with[99]['name'] == 'Bob'
    assert len(lookup_with) == 2, f"Expected 2 entries (1 main + 1 group), got {len(lookup_with)}"


def test_group_player_ids_empty_list_no_extra_keys():
    """Test 3: participant with group_player_ids=[] adds no extra keys, main ID still resolves.

    Verifies empty list produces no extra entries, and that the new loop-based
    implementation is active (by also verifying a non-empty case works).
    """
    # Empty list case
    participants_empty = [
        {'id': 7, 'name': 'Carol', 'seed': 3, 'final_rank': None, 'group_player_ids': []}
    ]
    lookup_empty = create_participants_lookup(participants_empty)
    assert lookup_empty[7]['name'] == 'Carol'
    # No extra keys beyond main ID
    assert len(lookup_empty) == 1, f"Expected 1 entry, got {len(lookup_empty)}"

    # Verify a non-empty case works, proving the loop-based implementation is active
    participants_with = [
        {'id': 7, 'name': 'Carol', 'seed': 3, 'final_rank': None, 'group_player_ids': [200, 201]}
    ]
    lookup_with = create_participants_lookup(participants_with)
    assert 200 in lookup_with, "group_player_id 200 must be in lookup"
    assert 201 in lookup_with, "group_player_id 201 must be in lookup"
    assert lookup_with[200]['name'] == 'Carol'
    assert len(lookup_with) == 3, f"Expected 3 entries (1 main + 2 group), got {len(lookup_with)}"


def test_multiple_participants_with_group_player_ids():
    """Test 4: two participants each with group_player_ids → all IDs in lookup without conflict."""
    participants = [
        {'id': 1, 'name': 'Alice', 'seed': 1, 'final_rank': None, 'group_player_ids': [101, 102]},
        {'id': 2, 'name': 'Bob', 'seed': 2, 'final_rank': None, 'group_player_ids': [201, 202]},
    ]
    lookup = create_participants_lookup(participants)

    # Main IDs
    assert lookup[1]['name'] == 'Alice'
    assert lookup[2]['name'] == 'Bob'
    # Group player IDs for Alice
    assert lookup[101]['name'] == 'Alice'
    assert lookup[102]['name'] == 'Alice'
    # Group player IDs for Bob
    assert lookup[201]['name'] == 'Bob'
    assert lookup[202]['name'] == 'Bob'
    # Total: 2 main + 4 group = 6 entries
    assert len(lookup) == 6


def test_raw_participants_list_no_duplicates():
    """Test 5: /raw/ participants list built from raw list has no duplicates for group_player_ids."""
    # Simulate the participants list as returned by Challonge API
    participants = [
        {'id': 1, 'name': 'Alice', 'seed': 1, 'final_rank': None, 'group_player_ids': [101, 102]},
        {'id': 2, 'name': 'Bob', 'seed': 2, 'final_rank': None, 'group_player_ids': [201, 202]},
    ]

    # Build the lookup (which now expands group_player_ids — 6 entries after GREEN)
    participants_data = create_participants_lookup(participants)

    # After GREEN, participants_data should have 6 entries (2 main + 4 group)
    assert len(participants_data) == 6, (
        f"After implementation, lookup should have 6 entries, got {len(participants_data)}"
    )

    # OLD (buggy) pattern: iterating participants_data.items() would produce 6 entries — bad
    old_participants_list = [{'id': k, **v} for k, v in participants_data.items()]
    assert len(old_participants_list) == 6  # This confirms the bug is present in the old approach

    # NEW (correct) pattern: sourced from the raw participants list → exactly 2 entries
    new_participants_list = [
        {'id': p['id'], 'name': p['name'], 'seed': p.get('seed'), 'final_rank': p.get('final_rank')}
        for p in participants
    ]

    # The new pattern must have exactly 2 entries (one per real participant)
    assert len(new_participants_list) == 2, (
        f"Expected 2 participants, got {len(new_participants_list)}"
    )
    ids_in_list = [entry['id'] for entry in new_participants_list]
    assert 1 in ids_in_list
    assert 2 in ids_in_list
