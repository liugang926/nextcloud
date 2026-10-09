#!/usr/bin/env python3
"""Real concurrent second QA/native replay in a fresh opted-in owned fixture.

Preparation alone proves nothing. The model scheduler delays an actual streamed
response, while all messages, origins, leases, frames and terminals come from
the normal application. This positive runner does not revoke the source share.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import queue
import runpy
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
HISTORY = runpy.run_path(str(HERE / 'synthetic-ldap-history-acceptance.py'))
Q = HISTORY['probe']
OWNER, E2E = Q['owner'], Q['e2e']
FILES = runpy.run_path(str(HERE / 'synthetic-ldap-resource-watch.py'))
require = Q['require']


class SSEReader:
    """Keep an actual HTTP connection and its real frames until EOF/terminal."""
    def __init__(self, request, seconds=60):
        self.request, self.seconds = request, seconds
        self.events, self.error_type, self.status = [], None, None
        self.eof_observed = False
        self.bytes, self.digest = 0, hashlib.sha256()
        self.closed = threading.Event()
        self.incoming = queue.Queue()
        self.response = None
        self.thread = threading.Thread(target=self._read, name='owned-native-replay-http', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def _read(self):
        deadline = time.monotonic() + self.seconds
        try:
            with urllib.request.urlopen(self.request, timeout=min(30, self.seconds)) as response:
                self.response, self.status = response, response.status
                require(self.status == 200, 'actual running HTTP did not return HTTP200')
                while time.monotonic() < deadline:
                    response.fp.raw._sock.settimeout(max(.1, deadline - time.monotonic()))
                    line = response.readline(65537)
                    if not line:
                        self.eof_observed = True
                        break
                    self.bytes += len(line)
                    self.digest.update(line)
                    require(self.bytes <= 2 * 1024 * 1024 and len(line) <= 65536,
                            'actual running SSE exceeded its bound')
                    if not line.startswith(b'data:') or line[5:].strip() in {b'', b'[DONE]'}:
                        continue
                    events = Q['parse_sse_events'](line)
                    for event in events:
                        require(len(self.events) < 1024, 'actual running SSE event bound exceeded')
                        self.events.append(event)
                        self.incoming.put(event)
                require(self.eof_observed, 'actual HTTP exceeded its total read deadline')
        except urllib.error.HTTPError as error:
            self.status, self.error_type = error.code, type(error).__name__
            error.close()
        except Exception as error:
            self.error_type = type(error).__name__
        finally:
            self.response = None
            self.closed.set()
            self.incoming.put(None)

    def wait_event(self, predicate, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.incoming.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            require(event is not None, 'actual HTTP ended before the running window was captured')
            require(event.get('response_type') != 'error', 'actual running stream emitted an error')
            if predicate(event):
                return event
        raise RuntimeError('actual running window was not observed before its deadline')

    def finish(self):
        self.thread.join(self.seconds + 5)
        require(not self.thread.is_alive(), 'actual HTTP reader did not terminate')
        require(self.error_type is None and self.status == 200, 'actual HTTP reader failed')
        return self.events

    def cancel_and_join(self):
        response = self.response
        if response is not None:
            try:
                response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass
        self.thread.join(35)
        return not self.thread.is_alive()

    def metadata(self):
        return {'http_status': self.status, 'error_type': self.error_type, 'event_count': len(self.events),
                'http_bytes': self.bytes, 'http_sha256': self.digest.hexdigest(),
                'reader_joined': not self.thread.is_alive(), 'transport_eof_observed': self.eof_observed,
                'terminal_types': [event.get('response_type') for event in self.events
                                   if event.get('done') is True and event.get('response_type') in {'error','complete'}]}


def fresh_stream_configuration(directory, state):
    maximum = state.get('mock_chat_stream_delay_max_seconds')
    require(type(maximum) is int and 0 < maximum <= 20 and
            isinstance(state.get('mock_model_code_sha256'), str), 'fresh_owned_stream_opt_in_required')
    OWNER['assert_owned_resources'](directory, state)
    return maximum


def model_control(directory, state, payload=None):
    fresh_stream_configuration(directory, state)
    config = json.loads((directory / 'compose.yaml').read_text())
    container = OWNER['docker_inspect']('container', state['project'] + '-mock-embedding-1')
    labels = container['Config']['Labels'] or {}
    require(labels.get(OWNER['OWNER_LABEL']) == state['owner_token'] and
            labels.get('com.docker.compose.project') == state['project'] and
            labels.get('com.docker.compose.service') == 'mock-embedding' and
            container['Config']['Image'] == config['services']['mock-embedding']['image'] and
            container['State']['Running'], 'owned_model_control_container_identity')
    image = subprocess.run(['docker','image','inspect',config['services']['mock-embedding']['image'],'--format','{{.Id}}'],
                           capture_output=True,text=True,timeout=10,check=False)
    require(image.returncode==0 and image.stdout.strip()==container['Image'],
            'owned_model_control_immutable_image_changed')
    # The control endpoint is only enabled for the fresh private opt-in and is
    # reachable only from this verified model container's own loopback.
    script = '''import json,sys,urllib.request
p=json.load(sys.stdin)
data=None if p is None else json.dumps(p).encode()
r=urllib.request.Request('http://127.0.0.1:8000/_fixture/chat-stream-control',data=data,
headers={'Content-Type':'application/json'})
with urllib.request.urlopen(r,timeout=5) as response: print(json.dumps(json.load(response)))
'''
    result = subprocess.run(['docker', 'exec', '-i', container['Id'], 'python', '-c', script],
                            input=json.dumps(payload).encode(), capture_output=True, timeout=10, check=False)
    require(result.returncode == 0, 'owned_actual_model_schedule_control_failed')
    OWNER['assert_owned_resources'](directory, state)
    return json.loads(result.stdout)


def actual_incomplete_metadata(state, session, message, request):
    session, message, request = (str(uuid.UUID(x)) for x in (session, message, request))
    value = E2E['sql_json'](state['project'] + '-wk-db-1',
        "SELECT jsonb_build_object('message_id',m.id,'session_id',m.session_id,'request_id',m.request_id,"
        "'role',m.role,'is_completed',m.is_completed,'material_receipts',"
        "(SELECT count(*) FROM message_material_receipts x WHERE x.message_id=m.id),'generation_origins',"
        "(SELECT count(*) FROM message_generation_origins x WHERE x.message_id=m.id)) FROM messages m "
        f"WHERE m.id='{message}' AND m.session_id='{session}' AND m.request_id='{request}'")
    require(value.get('role') == 'assistant' and value.get('is_completed') is False and
            value.get('material_receipts') == 1 and value.get('generation_origins') >= 1,
            'actual_assistant_running_original_state_required')
    return value


def legal_body(event):
    return ((event.get('response_type') == 'answer' and isinstance(event.get('content'), str) and event['content']) or
            (event.get('response_type') == 'references' and isinstance(event.get('data'), dict) and
             event['data'].get('references')))


def answer_prefix(event):
    return event.get('response_type')=='answer' and isinstance(event.get('content'),str) and bool(event['content'])


def actual_references(events):
    refs=[]
    for event in events:
        if event.get('response_type')=='references':
            data=event.get('data') or {}
            require(isinstance(data,dict),'actual_running_reference_data_invalid')
            values=data.get('references') or event.get('knowledge_references') or []
            require(isinstance(values,list),'actual_running_reference_list_invalid')
            refs.extend(values)
    return refs


def unchanged_model_window(status,operation,sequence,finished=False):
    current=status.get('operation') or {}
    require(status.get('stream_sequence')==sequence and current.get('claimed_stream_sequence')==sequence and
            current.get('operation_id')==operation and
            current.get('state')==('finished' if finished else 'waiting_after_first_delta'),
            'native_replay_changed_actual_model_call_or_delay_window')


def record_worker_cleanup(result,joined,marker_removed):
    result['all_real_reader_threads_joined']=all(joined)
    result['own_worker_marker_removed']=marker_removed
    if not all(joined) or not marker_removed:
        # Preserve the actual window observations, but never publish acceptance
        # when a real reader is still active or cleanup ownership is uncertain.
        result.update(running_replay_accepted=False,terminal=1,
                      cleanup_error_code='actual_running_workers_not_cleanly_joined')
    elif result.get('running_replay_accepted'):
        result.setdefault('terminal',0)


def running_frame_matches(first, second, request):
    require(first.get('response_type') == second.get('response_type') and legal_body(second),
            'actual running native frame is not the producer body frame')
    require(first.get('id') == second.get('id') == request, 'running replay producer request mismatch')
    if first['response_type'] == 'answer':
        require(first.get('content') == second.get('content'), 'running replay changed actual producer answer prefix')
    else:
        require(first.get('data', {}).get('references') == second.get('data', {}).get('references'),
                'running replay changed actual producer citations')


def run(scratch, delay_seconds, output):
    directory, state = OWNER['owned_state'](scratch)
    maximum = fresh_stream_configuration(directory, state)
    require(type(delay_seconds) is int and 0 < delay_seconds <= maximum, 'invalid_owned_running_delay')
    require(state['mode'] in {'direct', 'nested'}, 'unsupported_owned_running_ldap_mode')
    output = Path(output).resolve()
    if not output.exists():
        output.mkdir(mode=0o700)
    FILES['private'](output, True)
    require(output != directory and directory not in output.parents and not list(output.iterdir()),
            'running_evidence_must_be_new_and_outside_fixture_controls')
    runtime = json.loads((directory / 'runtime.json').read_text())
    require(runtime.get('publication_root', 'group_share') == 'group_share', 'owned_group_share_required')
    passwords = json.loads((directory / 'passwords.json').read_text())
    base, nc_base = (f"http://127.0.0.1:{state['ports'][key]}" for key in ('weknora', 'nextcloud'))
    token = Q['wait_ldap_login'](base, 'alice', passwords['alice'])
    human = Q['citation_url'](state['project'], runtime['knowledge_id'], runtime['file_id'], nc_base)
    require(not model_control(directory, state)['operation'], 'owned_model_schedule_not_idle')
    operation = str(uuid.uuid4())
    marker = directory / 'active-probe-workers.json'
    marker_raw = FILES['exclusive_json'](marker, {'marker': 'actual_running_replay_workers_v1',
        'pid': os.getpid(), 'project': state['project'], 'operation_id': operation,
        'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    info = marker.lstat(); marker_identity = (info.st_dev, info.st_ino)
    readers = []; armed = False; result = {'project': state['project'], 'running_replay_accepted': False,
        'source_midstream_revocation_accepted': False, 'configured_delay_seconds': delay_seconds,
        'frozen_model_code_sha256': state['mock_model_code_sha256']}
    try:
        # The first QA is not armed and is an ordinary real completed generation.
        session = Q['answer_and_citation'](base, token, runtime, human)
        first = HISTORY['history'](base, session, token, 1, runtime['knowledge_id'], human)
        pinned = Q['completed_answer_metadata'](first[0], runtime['knowledge_id'], human)
        schedule = model_control(directory, state, {'action': 'arm', 'operation_id': operation,
                                                     'delay_seconds': delay_seconds}); armed = True
        require(schedule['operation']['state'] == 'armed', 'actual_model_schedule_did_not_arm')
        payload = json.dumps({'query': 'What is the synthetic approval code?',
            'knowledge_ids': [runtime['knowledge_id']], 'agent_enabled': False,
            'summary_model_id': runtime['chat_model_id'], 'channel': 'web', 'disable_title': True}).encode()
        producer = SSEReader(urllib.request.Request(base + '/api/v1/knowledge-chat/' + session,
            data=payload, headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})).start()
        readers.append(producer)
        identity = producer.wait_event(lambda e: e.get('response_type') == 'agent_query')
        require(identity.get('session_id') == session and isinstance(identity.get('assistant_message_id'), str),
                'actual_agent_query_has_no_owned_producer_identity')
        message, request = identity['assistant_message_id'], identity['id']
        body = producer.wait_event(answer_prefix)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            scheduled = model_control(directory, state)['operation']
            if scheduled.get('operation_id') == operation and scheduled.get('state') == 'waiting_after_first_delta':
                break
            require(not producer.closed.is_set(), 'actual_producer_completed_before_the_running_window')
            time.sleep(.1)
        else:
            raise RuntimeError('actual_model_first_delta_delay_window_was_not_observed')
        sequence=scheduled['claimed_stream_sequence']
        before_schedule=model_control(directory,state)
        unchanged_model_window(before_schedule,operation,sequence)
        producer_refs=actual_references(producer.events)
        Q['reference_metadata'](producer_refs,runtime['knowledge_id'],human)
        before = actual_incomplete_metadata(state, session, message, request)
        require(not producer.closed.is_set() and producer.thread.is_alive(), 'actual_producer_is_not_running')
        native = SSEReader(urllib.request.Request(base + '/api/v1/sessions/continue-stream/' + session + '?' +
            urllib.parse.urlencode({'message_id': message}), headers={'Authorization': 'Bearer ' + token})).start()
        readers.append(native)
        native_identity = native.wait_event(lambda e: e.get('response_type') == 'agent_query')
        require(native_identity.get('id') == request and native_identity.get('session_id') == session and
                native_identity.get('assistant_message_id') == message, 'actual_native_running_identity_changed')
        replay_body = native.wait_event(answer_prefix)
        running_frame_matches(body, replay_body, request)
        native_refs=actual_references(native.events)
        require(native_refs==producer_refs,'actual_running_native_changed_producer_reference_material')
        after = actual_incomplete_metadata(state, session, message, request)
        after_schedule=model_control(directory,state)
        unchanged_model_window(after_schedule,operation,sequence)
        require(not producer.closed.is_set(), 'actual_native_body_arrived_after_producer_completion')
        result['actual_running_window'] = {'before_native_connect': before, 'after_native_body': after,
            'actual_model_schedule': scheduled, 'actual_body_type': body['response_type'],
            'actual_body_sha256': hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
            'actual_reference_sha256':Q['reference_metadata'](producer_refs,runtime['knowledge_id'],human)['references_sha256'],
            'before_native_model_schedule':before_schedule,'after_native_model_schedule':after_schedule}
        model_control(directory, state, {'action': 'release', 'operation_id': operation})
        producer_events, native_events = producer.finish(), native.finish()
        finished_schedule=model_control(directory,state)
        unchanged_model_window(finished_schedule,operation,sequence,finished=True)
        result['actual_finished_model_schedule']=finished_schedule
        generated = Q['answer_stream_metadata'](producer_events, runtime['knowledge_id'], human)
        replayed = Q['answer_stream_metadata'](native_events, runtime['knowledge_id'], human)
        second = HISTORY['history'](base, session, token, 2, runtime['knowledge_id'], human)
        old = next(row for row in second if row['id'] == first[0]['id'])
        require(Q['completed_answer_metadata'](old, runtime['knowledge_id'], human) == pinned,
                'actual_second_qa_changed_first_original_answer')
        current = next(row for row in second if row['id'] == message)
        material = Q['completed_answer_metadata'](current, runtime['knowledge_id'], human)
        Q['require_same_answer_material'](generated, material)
        Q['require_same_answer_material'](replayed, material)
        for row in second:
            status, raw = HISTORY['replay'](base, session, row['id'], token)
            require(status == 200, 'actual_completed_native_replay_http_failed')
            complete = Q['answer_stream_metadata'](Q['parse_sse_events'](raw), runtime['knowledge_id'], human,
                                                    saved_content=row['content'], native_replay=True)
            Q['require_same_answer_material'](complete, Q['completed_answer_metadata'](row, runtime['knowledge_id'], human))
        result.update(session_id=session, actual_qa_rounds=2, completed_history_count=2, completed_replays=2,
            first_answer_preserved=True, live_qa_complete_terminals=2, original_citations=True,
            source_revocation_checked=False, running_replay_accepted=True,
            completed_history_messages=sorted([Q['completed_answer_metadata'](row,runtime['knowledge_id'],human)
                                               for row in second],key=lambda row:row['message_id']))
    except Exception as error:
        result.update(terminal=1, error_type=type(error).__name__, safe_reason=str(error)
                      if isinstance(error, RuntimeError) else 'actual_http_diagnostic_required')
        raise
    finally:
        if armed:
            try:
                model_control(directory, state, {'action': 'release', 'operation_id': operation})
            except Exception:
                pass
        joined = [reader.cancel_and_join() for reader in readers]
        result['actual_http_readers'] = [reader.metadata() for reader in readers]
        marker_removed=(FILES['remove_own_marker'](marker, marker_raw, marker_identity) if all(joined) else False)
        record_worker_cleanup(result,joined,marker_removed)
        FILES['exclusive_json'](output / 'actual-running-phase.metadata.json', result)
    require(result['all_real_reader_threads_joined'] and result['own_worker_marker_removed'],
            'actual_running_workers_not_cleanly_joined')
    FILES['exclusive_json'](output / 'actual-history.receipt.json', result)
    print(json.dumps({'project': state['project'], 'terminal': 0, 'evidence': str(output),
                      'running_replay_accepted': True, 'source_midstream_revocation_accepted': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--delay-seconds', type=int, default=20)
    args = parser.parse_args()
    try:
        run(args.scratch, args.delay_seconds, args.output)
    except Exception as error:
        print('Owned actual running replay refused: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
