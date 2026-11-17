"""Newcomer-only grouping never reallocates an existing member."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'plugin' / 'open_tech_quest' / 'waiting_math.py'
if not path.exists():
    path = Path('/opt/CTFd/CTFd/plugins/open_tech_quest/waiting_math.py')
spec = importlib.util.spec_from_file_location('waiting_math_rules', path)
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


class WaitingPlacementMath(unittest.TestCase):
    def test_fill_smaller_existing_teams_then_balance_new_teams(self):
        teams = [{'id': 1, 'name': 'Published', 'member_ids': [1, 2]},
                 {'id': 2, 'name': 'Other', 'member_ids': [3, 4, 5]}]
        before = repr(teams)
        groups = rules.placement(list(range(10, 20)), teams, 4, ['Team 1', 'team 2', 'Other', 'Published'])
        self.assertEqual([(g['team_id'], len(g['user_ids'])) for g in groups], [(1, 2), (2, 1), (None, 4), (None, 3)])
        self.assertEqual([g['name'] for g in groups[2:]], ['Team 3', 'Team 4'])
        self.assertEqual(sorted(u for g in groups for u in g['user_ids']), list(range(10, 20)))
        self.assertEqual(repr(teams), before)

    def test_small_batches_empty_queue_and_capacity_one(self):
        teams = [{'id': 5, 'name': 'Five', 'member_ids': [1]},
                 {'id': 2, 'name': 'Two', 'member_ids': [2, 3, 4]}]
        self.assertEqual(rules.placement([], teams, 4, []), [])
        self.assertEqual(rules.placement([10], teams, 4, [t['name'] for t in teams]),
                         [{'team_id': 5, 'name': 'Five', 'user_ids': [10]}])
        self.assertEqual([len(g['user_ids']) for g in rules.placement([1, 2, 3], [], 1, [])], [1, 1, 1])
        with self.assertRaises(ValueError):
            rules.placement([1], [], 0, [])
