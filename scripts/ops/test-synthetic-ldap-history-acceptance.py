#!/usr/bin/env python3
"""Pure HTTP payload acceptance regressions; no runtime QA/replay is claimed."""
import copy
import json
from pathlib import Path
import runpy
import sys
import unittest
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
Q = runpy.run_path(str(HERE / 'synthetic-ldap-ask-handoff.py'))
R = runpy.run_path(str(HERE / 'synthetic-ldap-normal-restore.py'))


class ActualAnswerCriteriaTest(unittest.TestCase):
    def setUp(self):
        self.knowledge = str(uuid.uuid4())
        self.human = 'http://127.0.0.1:18082/index.php/f/92'
        self.answer = 'The actual answer is ' + Q['MARKER']
        self.request, self.session, self.message = (str(uuid.uuid4()) for _ in range(3))
        self.refs = [{'knowledge_id': self.knowledge,
                      'content': Q['MARKER'], 'metadata': {'nextcloud_human_url': self.human}}]
        self.row = {'id': self.message, 'request_id': self.request, 'session_id': self.session,
                    'role': 'assistant', 'is_completed': True,
                    'content': self.answer, 'knowledge_references': self.refs}
        self.events = [{'id': self.request, 'response_type': 'agent_query', 'done': False,
                        'session_id': self.session, 'assistant_message_id': self.message},
                       {'id': self.request, 'response_type': 'references', 'done': False, 'data': {'references': self.refs}},
                       {'id': self.request, 'response_type': 'answer', 'done': True, 'content': self.answer},
                       {'id': self.request, 'response_type': 'complete', 'done': True, 'data': {'final_content': self.answer}}]

    def test_reference_marker_is_not_a_saved_answer(self):
        row = {**self.row, 'content': ''}
        self.assertIn(Q['MARKER'], json.dumps(row))  # The previous broad criterion passed.
        with self.assertRaisesRegex(RuntimeError, 'assistant content'):
            Q['completed_answer_metadata'](row, self.knowledge, self.human)

    def test_user_body_and_incomplete_assistant_are_not_completed_answers(self):
        for change in ({'role': 'user'}, {'is_completed': False}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                Q['completed_answer_metadata']({**self.row, **change}, self.knowledge, self.human)

    def test_actual_saved_answer_metadata_contains_only_hashes_ids_counts(self):
        material = Q['completed_answer_metadata'](self.row, self.knowledge, self.human)
        self.assertEqual(material['message_id'], self.row['id'])
        self.assertEqual(material['answer_bytes'], len(self.answer.encode()))
        self.assertNotIn(self.answer, json.dumps(material))
        self.assertNotIn(self.human, json.dumps(material))

    def test_same_first_message_id_does_not_hide_changed_answer_or_citation(self):
        pinned = Q['completed_answer_metadata'](self.row, self.knowledge, self.human)
        changed = {**self.row, 'content': self.answer + ' rewritten'}
        self.assertEqual(changed['id'], self.row['id'])
        self.assertNotEqual(Q['completed_answer_metadata'](changed, self.knowledge, self.human), pinned)
        changed_refs = copy.deepcopy(self.row)
        changed_refs['knowledge_references'][0]['content'] += ' changed original reference'
        self.assertNotEqual(Q['completed_answer_metadata'](changed_refs, self.knowledge, self.human), pinned)

    def test_cross_document_and_missing_citation_fail(self):
        for refs in ([], [{'knowledge_id': str(uuid.uuid4()), 'metadata': {'nextcloud_human_url': self.human}}],
                     [{'knowledge_id': self.knowledge, 'metadata': {'nextcloud_human_url': self.human + '?changed'}}]):
            with self.subTest(refs=refs), self.assertRaises(RuntimeError):
                Q['completed_answer_metadata']({**self.row, 'knowledge_references': refs}, self.knowledge, self.human)

    def test_valid_actual_stream_and_saved_replay_agree(self):
        stream = Q['answer_stream_metadata'](self.events, self.knowledge, self.human,
                                             saved_content=self.answer, native_replay=True)
        saved = Q['completed_answer_metadata'](self.row, self.knowledge, self.human)
        self.assertEqual(stream['answer_sha256'], saved['answer_sha256'])
        Q['require_same_answer_material'](stream, saved)
        self.assertTrue(stream['successful_complete_terminal'])

    def test_marker_in_references_or_error_is_not_a_successful_stream(self):
        for events in ([*self.events[:2], self.events[-1]],
                       [*self.events[:2], {**self.events[2], 'content': '', 'data': {'copied': Q['MARKER']}}, self.events[-1]],
                       [*self.events, {'response_type': 'error', 'done': True, 'content': Q['MARKER']}]):
            with self.subTest(events=events), self.assertRaises(RuntimeError):
                Q['answer_stream_metadata'](events, self.knowledge, self.human)

    def test_transport_eof_or_partial_answer_is_not_successful_complete(self):
        for events in (self.events[:-1], [*self.events[:-1], {**self.events[-1], 'done': False}]):
            with self.subTest(events=events), self.assertRaisesRegex(RuntimeError, 'complete terminal'):
                Q['answer_stream_metadata'](events, self.knowledge, self.human)

    def test_native_replay_cannot_replace_saved_body_using_current_source(self):
        changed = copy.deepcopy(self.events)
        changed[2]['content'] += ' newly generated from current source'
        changed[3]['data']['final_content'] = changed[2]['content']
        with self.assertRaisesRegex(RuntimeError, 'saved original answer'):
            Q['answer_stream_metadata'](changed, self.knowledge, self.human,
                                        saved_content=self.answer, native_replay=True)
        changed[3]['data']['final_content'] = self.answer
        with self.assertRaisesRegex(RuntimeError, 'differs from its answer'):
            Q['answer_stream_metadata'](changed, self.knowledge, self.human, native_replay=True)

    def test_incomplete_finish_and_content_after_complete_are_not_success(self):
        for events in ([*self.events[:-1], {**self.events[-1], 'finish_reason': 'incomplete'}],
                       [*self.events[:-1], {**self.events[-1], 'data': {'finish_reason': 'incomplete'}}],
                       [*self.events, self.events[2]], [*self.events, self.events[1]],
                       [*self.events, {**self.events[-1], 'done': False}]):
            with self.subTest(events=events), self.assertRaises(RuntimeError):
                Q['answer_stream_metadata'](events, self.knowledge, self.human)

    def test_identical_answer_text_cannot_replace_another_actual_producer(self):
        material = Q['answer_stream_metadata'](self.events, self.knowledge, self.human)
        for change in ({'id': str(uuid.uuid4())}, {'request_id': str(uuid.uuid4())},
                       {'session_id': str(uuid.uuid4())}):
            saved = Q['completed_answer_metadata']({**self.row, **change}, self.knowledge, self.human)
            self.assertEqual(saved['answer_sha256'], material['answer_sha256'])
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, 'producer'):
                Q['require_same_answer_material'](material, saved)
        mixed = copy.deepcopy(self.events)
        mixed[2]['id'] = str(uuid.uuid4())
        with self.assertRaisesRegex(RuntimeError, 'request IDs'):
            Q['answer_stream_metadata'](mixed, self.knowledge, self.human)

    def test_replay_citation_material_must_match_actual_saved_references(self):
        altered = copy.deepcopy(self.events)
        altered[1]['data']['references'][0]['content'] += ' replaced original source quote'
        streamed = Q['answer_stream_metadata'](altered, self.knowledge, self.human)
        saved = Q['completed_answer_metadata'](self.row, self.knowledge, self.human)
        with self.assertRaisesRegex(RuntimeError, 'citations'):
            Q['require_same_answer_material'](streamed, saved)

    def test_sse_parser_preserves_actual_frames_and_rejects_invalid_payload(self):
        raw = b'event: message\n' + b''.join(('data:' + json.dumps(x) + '\n\n').encode() for x in self.events)
        self.assertEqual(Q['parse_sse_events'](raw), self.events)
        for raw in (b'data:not-json\n', b'data:[]\n', b'data:\xff\n'):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                Q['parse_sse_events'](raw)

    def test_clean_restore_requires_an_actual_two_round_baseline(self):
        baseline = {'project': 'nc-synldap-abcdef12', 'session_id': str(uuid.uuid4()),
                    'actual_qa_rounds': 2, 'completed_history_count': 2, 'first_answer_preserved': True,
                    'live_qa_complete_terminals': 2}
        R['actual_two_round_baseline'](baseline, baseline['project'])
        for change in ({'completed_history_count': 1}, {'actual_qa_rounds': 1},
                       {'completed_history_count': 2.0}, {'first_answer_preserved': False},
                       {'live_qa_complete_terminals': 0}, {'project': 'foreign'}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                R['actual_two_round_baseline']({**baseline, **change}, baseline['project'])


if __name__ == '__main__':
    unittest.main()
