"""Tests for tools/remind_nick: weekly email of the open items in docs/REMINDERS.md."""

import sys
from pathlib import Path

# tools/ is not on the default path
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
import remind_nick as rn

SAMPLE = """# Reminders for Nick

Intro text that is not an item.

## Open

- [ ] (d) Re-pair the tumble dryer plug.
  Second line of the same item.
- [x] (z) Something already done.
- [ ] (e) Metering plugs for the dishwasher and washing machine.

## Done

- [x] (y) Old item.
"""


class TestOpenItems:
    def test_returns_unchecked_items_with_continuation_lines(self):
        assert rn.open_items(SAMPLE) == [
            '(d) Re-pair the tumble dryer plug.\nSecond line of the same item.',
            '(e) Metering plugs for the dishwasher and washing machine.',
        ]

    def test_no_open_items(self):
        assert rn.open_items('# Reminders\n\n- [x] (d) done\n') == []


class TestMessage:
    def test_message_lists_items_and_names_the_file(self):
        subject, body = rn.message(['(d) one', '(e) two'])
        assert '2' in subject
        assert '(d) one' in body and '(e) two' in body
        assert 'docs/REMINDERS.md' in body

    def test_nothing_open_sends_nothing(self):
        sent = []
        assert rn.run(SAMPLE.replace('- [ ]', '- [x]'), send=lambda s, b: sent.append(s) or True) == 0
        assert sent == []

    def test_open_items_are_sent_once(self):
        sent = []
        assert rn.run(SAMPLE, send=lambda s, b: sent.append((s, b)) or True) == 0
        assert len(sent) == 1

    def test_failed_send_is_an_error(self):
        assert rn.run(SAMPLE, send=lambda s, b: False) == 1
