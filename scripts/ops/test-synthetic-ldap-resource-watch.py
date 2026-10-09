#!/usr/bin/env python3
"""Offline resource observer guards, including the real shell /proc parser."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

P = runpy.run_path(str(Path(__file__).with_name("synthetic-ldap-resource-watch.py")))


class ResourceWatchTests(unittest.TestCase):
    def setUp(self):
        # Method tests cannot contact the host's Docker daemon through new reads.
        patch = mock.patch.dict(P['OWNER'], {'docker_names': mock.Mock(return_value=set())})
        patch.start()
        self.addCleanup(patch.stop)
        real_run = subprocess.run
        def forbid_host_docker(args, *positional, **kwargs):
            if isinstance(args, (tuple, list)) and args and args[0] == 'docker':
                raise AssertionError('offline observer tests must not contact host Docker')
            return real_run(args, *positional, **kwargs)
        patch = mock.patch.object(subprocess, 'run', side_effect=forbid_host_docker)
        patch.start()
        self.addCleanup(patch.stop)

    def transition_observer(self):
        observer = object.__new__(P["Observer"])
        observer.directory = Path('/private/tmp/fixture-method-only')
        observer.state = {"project": "nc-synldap-abcdef12", "owner_token": "o" * 32,
                          "weknora_image_id": "sha256:" + "c" * 64}
        observer.identity = dict(observer.state)
        observer.ids = {}
        observer.image_ids = {"wk-app": observer.state['weknora_image_id'], "nextcloud": "sha256:" + "d" * 64}
        for role in P['OWNER']['SERVICES']:
            observer.image_ids.setdefault(role, 'sha256:' + 'e' * 64)
        observer.config = {'services': {role: {'image': image, 'mem_limit': 16*1024**2,
            'memswap_limit': 16*1024**2, 'cpus': 1.0} for role, image in observer.image_ids.items()}}
        observer.owner_disappearance_rescans = 0
        observer.owner_snapshot_rescans = 0
        observer.last_docker_failure = None
        return observer

    def missing_oneoff(self, observer, name=None, stderr=None):
        name = name or observer.state['project'] + '-wk-app-99'
        return subprocess.CalledProcessError(1, ['docker', 'container', 'inspect', name],
            stderr=stderr or 'Error response from daemon: No such container: ' + name + '\n')

    def snapshot_resource(self, observer, kind, role):
        project = observer.state['project']; own = P['OWNER']
        name = project + ('-' + role + '-1' if kind == 'container' else '_' + role)
        labels = {'com.docker.compose.project': project, own['OWNER_LABEL']: observer.state['owner_token']}
        if kind == 'container':
            labels['com.docker.compose.service'] = role
            expected = observer.config['services'][role]
            return name, {'Id': 'a'*64, 'Name': '/'+name, 'Image': observer.image_ids[role],
                'Config': {'Labels': labels}, 'HostConfig': {'Memory': expected['mem_limit'],
                    'MemorySwap': expected['memswap_limit'], 'NanoCpus': int(expected['cpus']*1_000_000_000)}}
        labels['com.docker.compose.'+kind] = role
        return name, {'Id': 'b'*64, 'Name': name, 'Labels': labels}

    @contextlib.contextmanager
    def real_prefix_flow(self, observer, kind, name, item, extra=None, inspect_error=None):
        """Real strict helper sees a creation between its two fake Docker lists."""
        own = P['OWNER']; strict = own['assert_owned_resources']; globals_ = strict.__globals__
        inventory = {k: set() for k in ('container', 'volume', 'network')}
        inventory[kind].add(name)
        for k, values in (extra or {}).items(): inventory[k].update(values)
        labelled_target_reads = 0
        def names(k, project=None):
            nonlocal labelled_target_reads
            if k == kind and project is not None:
                labelled_target_reads += 1
                if labelled_target_reads == 1: return set()
            return set(inventory[k])
        def inspect(k, candidate):
            if k == kind and candidate == name:
                if inspect_error: raise inspect_error
                return item
            raise AssertionError('unexpected inspection; unknown names must be rejected before inspect')
        wrapped = mock.Mock(wraps=strict)
        with mock.patch.dict(globals_, {'docker_names': names, 'docker_inspect': inspect}), \
                mock.patch.dict(own, {'owned_state': lambda path: (path, observer.state),
                    'assert_owned_resources': wrapped, 'docker_names': names, 'docker_inspect': inspect}):
            yield wrapped

    def test_all_startup_container_roles_and_volume_network_creation_rescan(self):
        cases = [('container', role) for role in sorted(P['OWNER']['SERVICES'] | {'wk-ui'})]
        cases += [('volume', role) for role in sorted(P['OWNER']['VOLUMES'] | P['OWNER']['BODY_VOLUMES'])]
        cases += [('network', 'default')]
        for kind, role in cases:
            observer = self.transition_observer(); observer.state.update(resource_profile='normal-trial',
                body_journal=True, weknora_ui_image='fixture-ui', weknora_ui_image_id='sha256:'+'f'*64)
            observer.image_ids['wk-ui'] = observer.state['weknora_ui_image_id']
            observer.config['services']['wk-ui'] = {'image': 'fixture-ui', 'mem_limit': 16*1024**2,
                'memswap_limit': 16*1024**2, 'cpus': 1.0}
            observer.identity = dict(observer.state)
            name, item = self.snapshot_resource(observer, kind, role)
            with self.subTest(kind=kind, role=role), self.real_prefix_flow(observer, kind, name, item) as strict:
                observer.verify(); self.assertEqual(strict.call_count, 2)
            self.assertEqual(observer.owner_snapshot_rescans, 1)
            self.assertEqual(observer.ids, {})
            self.assertEqual(observer.last_owner_prefix_mismatch['kind'], kind)
            self.assertEqual(observer.last_owner_prefix_mismatch['expected_resource_names'], [name])

    def test_typed_mismatch_does_not_relax_or_retry_mutation_helper(self):
        observer = self.transition_observer(); name, item = self.snapshot_resource(observer, 'container', 'nextcloud')
        with self.real_prefix_flow(observer, 'container', name, item) as strict:
            with self.assertRaises(P['OWNER']['PrefixResourceMismatch']) as caught:
                strict(observer.directory, observer.state)
            self.assertIsInstance(caught.exception, RuntimeError)
            self.assertEqual(str(caught.exception), 'unlabeled resource occupies synthetic Compose project name')
            self.assertEqual(caught.exception.kind, 'container')
            self.assertEqual(caught.exception.names, (name,))
            self.assertEqual(strict.call_count, 1)

    def test_nonprimary_image_pin_is_read_from_frozen_reference_without_environment(self):
        observer=self.transition_observer();name,item=self.snapshot_resource(observer,'container','wk-db')
        pin=observer.image_ids.pop('wk-db');observer.command=mock.Mock(return_value=(pin+'\n').encode())
        with self.real_prefix_flow(observer,'container',name,item):observer.verify()
        observer.command.assert_called_once_with(['docker','image','inspect','--format','{{.Id}}',
            observer.config['services']['wk-db']['image']])
        self.assertEqual(observer.image_ids['wk-db'],pin)
        self.assertEqual(observer.ids,{})

    def test_real_prefix_existing_foreign_owner_image_budget_and_captured_id_refused(self):
        for mode in ('nonce', 'project', 'service', 'name', 'image', 'memory', 'swap', 'cpu', 'captured'):
            observer = self.transition_observer(); observer.state['resource_profile'] = 'normal-trial'
            observer.identity = dict(observer.state)
            name, item = self.snapshot_resource(observer, 'container', 'wk-db' if mode != 'captured' else 'wk-app')
            if mode == 'nonce': item['Config']['Labels'][P['OWNER']['OWNER_LABEL']] = 'foreign'
            elif mode == 'project': item['Config']['Labels']['com.docker.compose.project'] = 'foreign'
            elif mode == 'service': item['Config']['Labels']['com.docker.compose.service'] = 'nextcloud'
            elif mode == 'name': item['Name'] = '/foreign'
            elif mode == 'image': item['Image'] = 'sha256:'+'f'*64
            elif mode == 'memory': item['HostConfig']['Memory'] += 1
            elif mode == 'swap': item['HostConfig']['MemorySwap'] += 1
            elif mode == 'cpu': item['HostConfig']['NanoCpus'] += 1
            elif mode == 'captured': observer.ids['wk-app'] = 'b'*64
            with self.subTest(mode=mode), self.real_prefix_flow(observer, 'container', name, item) as strict:
                with self.assertRaises(P['WatchError']): observer.verify()
                self.assertEqual(strict.call_count, 1)
            self.assertEqual(observer.owner_snapshot_rescans if hasattr(observer, 'owner_snapshot_rescans') else 0, 0)

    def test_current_union_checks_foreign_volume_network_and_unexpected_scope(self):
        for kind, role in [('volume','wk-data'), ('network','default')]:
            for mode in ('nonce', 'project', 'scope', 'name'):
                observer = self.transition_observer(); name, item = self.snapshot_resource(observer, kind, role)
                if mode == 'nonce': item['Labels'][P['OWNER']['OWNER_LABEL']] = 'foreign'
                elif mode == 'project': item['Labels']['com.docker.compose.project'] = 'foreign'
                elif mode == 'scope': item['Labels']['com.docker.compose.'+kind] = 'foreign'
                else: item['Name'] = 'foreign'
                with self.subTest(kind=kind, mode=mode), self.real_prefix_flow(observer, kind, name, item) as strict:
                    with self.assertRaises(P['WatchError']): observer.verify()
                    self.assertEqual(strict.call_count, 1)
        observer = self.transition_observer(); name, item = self.snapshot_resource(observer, 'container','wk-app')
        with self.real_prefix_flow(observer, 'container', name, item,
                extra={'volume': {observer.state['project']+'_unexpected-body-marker'}}) as strict:
            with self.assertRaisesRegex(P['WatchError'], 'owner_prefix_unknown_resource'): observer.verify()
            self.assertEqual(strict.call_count, 1)

    def test_unknown_prefix_diagnostic_hashes_name_without_plaintext_or_retry(self):
        observer = self.transition_observer(); name = observer.state['project']+'-untrusted-secret-marker-1'
        own = P['OWNER']; failure = mock.Mock(side_effect=own['PrefixResourceMismatch']('container', {name}))
        with mock.patch.dict(own, {'owned_state': lambda path: (path, observer.state), 'assert_owned_resources': failure}):
            with self.assertRaisesRegex(P['WatchError'], 'owner_prefix_unknown_resource'): observer.verify()
        self.assertEqual(failure.call_count, 1)
        fact = observer.last_owner_prefix_mismatch
        self.assertEqual(fact['unknown_resource_count'], 1)
        self.assertEqual(fact['expected_resource_names'], [])
        self.assertNotIn(name, json.dumps(fact))

    def test_other_replicas_and_unapproved_oneoff_are_unknown_even_if_owned(self):
        for suffix in ('-wk-app-2', '-wk-db-99'):
            observer = self.transition_observer(); own = P['OWNER']; name = observer.state['project']+suffix
            failure = mock.Mock(side_effect=own['PrefixResourceMismatch']('container',{name}))
            with self.subTest(suffix=suffix), mock.patch.dict(own, {
                    'owned_state':lambda path:(path,observer.state),'assert_owned_resources':failure}):
                with self.assertRaisesRegex(P['WatchError'],'owner_prefix_unknown_resource'):observer.verify()
                self.assertEqual(failure.call_count,1)
            self.assertEqual(observer.owner_snapshot_rescans,0)

    def test_all_kinds_exact_disappeared_candidates_allow_fresh_empty_snapshot(self):
        for kind, role in [('container','wk-db'), ('volume','wk-data'), ('network','default')]:
            observer = self.transition_observer(); name, _ = self.snapshot_resource(observer, kind, role)
            own = P['OWNER']; mismatch = own['PrefixResourceMismatch'](kind, {name})
            error = subprocess.CalledProcessError(1, ['docker',kind,'inspect',name],
                stderr='Error: No such '+kind+': '+name+'\n')
            strict = mock.Mock(side_effect=[mismatch, None])
            with self.subTest(kind=kind), mock.patch.dict(own, {'owned_state': lambda path: (path, observer.state),
                    'assert_owned_resources': strict, 'docker_inspect': mock.Mock(side_effect=error)}):
                observer.verify(); self.assertEqual(strict.call_count, 2)
            self.assertEqual(observer.owner_snapshot_rescans, 1)
            self.assertEqual(observer.owner_disappearance_rescans, 1)

    def test_prefix_daemon_or_wrong_notfound_never_rescans(self):
        for mode in ('daemon', 'wrong-name', 'returncode'):
            observer = self.transition_observer(); name, item = self.snapshot_resource(observer,'container','nextcloud')
            error = self.missing_oneoff(observer, name)
            if mode == 'daemon': error.stderr = 'Cannot connect to Docker daemon; secret-marker'
            elif mode == 'wrong-name': error.stderr = 'Error: No such container: other'
            else: error.returncode = 125
            with self.subTest(mode=mode), self.real_prefix_flow(observer, 'container', name, item, inspect_error=error) as strict:
                with self.assertRaisesRegex(P['WatchError'], 'owner_readonly_docker_command_failed'): observer.verify()
                self.assertEqual(strict.call_count, 1)

    def test_persistent_prefix_mismatch_is_bounded_and_no_id_is_adopted(self):
        observer = self.transition_observer(); name, item = self.snapshot_resource(observer,'container','nextcloud')
        own=P['OWNER']; failure=mock.Mock(side_effect=own['PrefixResourceMismatch']('container',{name}))
        with mock.patch.dict(own, {'owned_state': lambda path:(path,observer.state),
                'assert_owned_resources':failure,'docker_inspect':mock.Mock(return_value=item)}):
            with self.assertRaisesRegex(P['WatchError'], 'owner_prefix_snapshot_rescan_exhausted'): observer.verify()
        self.assertEqual(failure.call_count,3);self.assertEqual(observer.owner_snapshot_rescans,2)
        self.assertEqual(observer.ids,{})

    def test_fresh_listed_resource_disappearance_exhausts_without_accepting_snapshot(self):
        observer=self.transition_observer();own=P['OWNER'];name,_=self.snapshot_resource(observer,'volume','wk-data')
        error=subprocess.CalledProcessError(1,['docker','volume','inspect',name],
            stderr='Error response from daemon: get '+name+': no such volume\n')
        strict=mock.Mock(side_effect=[own['PrefixResourceMismatch']('volume',{name}),None,None])
        names=lambda kind,project=None: {name} if kind=='volume' else set()
        with mock.patch.dict(own, {'owned_state':lambda path:(path,observer.state),
                'assert_owned_resources':strict,'docker_names':names,'docker_inspect':mock.Mock(side_effect=error)}):
            with self.assertRaisesRegex(P['WatchError'],'owner_prefix_snapshot_rescan_exhausted'):observer.verify()
        self.assertEqual(strict.call_count,3);self.assertEqual(observer.owner_snapshot_rescans,2)
        self.assertEqual(observer.ids,{})

    def test_listed_ephemeral_disappearance_rescans_complete_strict_snapshot(self):
        observer = self.transition_observer()
        own = P['OWNER']
        with mock.patch.dict(own, {'owned_state': lambda path: (path, observer.state)}), \
                mock.patch.dict(own, {'assert_owned_resources': mock.Mock(side_effect=[self.missing_oneoff(observer), None]),
                                      'docker_inspect': mock.Mock(side_effect=self.missing_oneoff(observer))}):
            observer.verify()
            self.assertEqual(own['assert_owned_resources'].call_count, 2)
        self.assertEqual(observer.owner_disappearance_rescans, 1)
        self.assertEqual(observer.ids, {})
        self.assertEqual(observer.last_docker_failure['stderr_summary'], 'listed_resource_disappeared')
        self.assertEqual(observer.last_docker_failure['command_argv'],
                         ['docker', 'container', 'inspect', observer.state['project'] + '-wk-app-99'])
        self.assertEqual(observer.last_docker_failure['command_returncode'], 1)
        self.assertNotIn('rss', json.dumps(observer.last_docker_failure).lower())

    def test_reappeared_ephemeral_requires_nonce_image_and_frozen_budget(self):
        for mode in ('owned', 'nonce', 'image', 'budget'):
            observer = self.transition_observer(); own = P['OWNER']
            observer.state['resource_profile'] = 'normal-trial'; observer.identity = dict(observer.state)
            observer.config = {'services': {'nextcloud': {'mem_limit': 2*1024**3, 'memswap_limit': 2*1024**3, 'cpus': 1.0}}}
            name = observer.state['project'] + '-nextcloud-99'
            item = {'Id': 'b'*64, 'Name': '/'+name, 'Image': observer.image_ids['nextcloud'],
                    'Config': {'Labels': {'com.docker.compose.project': observer.state['project'],
                        'com.docker.compose.service': 'nextcloud', own['OWNER_LABEL']: observer.state['owner_token']}},
                    'HostConfig': {'Memory': 2*1024**3, 'MemorySwap': 2*1024**3, 'NanoCpus': 1000000000}}
            if mode == 'nonce': item['Config']['Labels'][own['OWNER_LABEL']] = 'foreign'
            elif mode == 'image': item['Image'] = 'sha256:'+'e'*64
            elif mode == 'budget': item['HostConfig']['Memory'] = 3*1024**3
            with self.subTest(mode=mode), mock.patch.dict(own, {
                    'owned_state': lambda path: (path, observer.state),
                    'assert_owned_resources': mock.Mock(side_effect=[self.missing_oneoff(observer, name), None]),
                    'docker_inspect': mock.Mock(return_value=item)}):
                if mode == 'owned': observer.verify()
                else:
                    with self.assertRaisesRegex(P['WatchError'], 'snapshot_container_(owner|image|budget)_changed'):
                        observer.verify()
            self.assertEqual(observer.ids, {})

    def test_continuing_ephemeral_disappearance_is_bounded_and_refused(self):
        observer = self.transition_observer(); own = P['OWNER']
        failure = mock.Mock(side_effect=lambda *_: (_ for _ in ()).throw(self.missing_oneoff(observer)))
        with mock.patch.dict(own, {'owned_state': lambda path: (path, observer.state),
                'assert_owned_resources': failure, 'docker_inspect': mock.Mock(side_effect=self.missing_oneoff(observer))}):
            with self.assertRaisesRegex(P['WatchError'], 'owner_ephemeral_disappearance_rescan_exhausted'):
                observer.verify()
        self.assertEqual(failure.call_count, 3)
        self.assertEqual(observer.owner_disappearance_rescans, 2)
        self.assertEqual(observer.ids, {})

    def test_primary_unsafe_or_daemon_failures_never_rescan(self):
        for mode in ('captured-primary', 'unsafe-name', 'daemon', 'wrong-missing-name', 'nonzero-other'):
            observer = self.transition_observer(); own = P['OWNER']
            name = observer.state['project'] + '-wk-app-99'
            if mode == 'captured-primary':
                name = observer.state['project'] + '-wk-app-1'
                observer.ids['wk-app'] = 'a' * 64
            elif mode == 'unsafe-name': name = observer.state['project'] + '-unknown-99'
            error = self.missing_oneoff(observer, name)
            if mode == 'daemon': error.stderr = 'Cannot connect to the Docker daemon: private-untrusted-text'
            elif mode == 'wrong-missing-name': error.stderr = 'Error: No such object: some-other-resource'
            elif mode == 'nonzero-other': error.returncode = 125
            failure = mock.Mock(side_effect=error)
            with self.subTest(mode=mode), mock.patch.dict(own, {
                    'owned_state': lambda path: (path, observer.state), 'assert_owned_resources': failure}):
                with self.assertRaisesRegex(P['WatchError'], 'owner_readonly_docker_command_failed|captured_container_missing'):
                    observer.verify()
            self.assertEqual(failure.call_count, 1)
            self.assertEqual(observer.owner_disappearance_rescans, 0)

    def test_disappeared_then_existing_foreign_nonce_or_image_still_refused(self):
        observer = self.transition_observer(); own = P['OWNER']
        strict = own['assert_owned_resources']
        globals_ = strict.__globals__
        name = observer.state['project'] + '-wk-app-99'
        for mode in ('nonce', 'image'):
            item = {'Id': 'a'*64, 'Name': '/' + name, 'Image': observer.state['weknora_image_id'], 'Config': {'Labels': {
                'com.docker.compose.project': observer.state['project'], own['OWNER_LABEL']: observer.state['owner_token'],
                'com.docker.compose.service': 'wk-app'}}}
            if mode == 'nonce': item['Config']['Labels'][own['OWNER_LABEL']] = 'foreign'
            else: item['Image'] = 'sha256:' + 'f'*64
            inspect = mock.Mock(side_effect=[self.missing_oneoff(observer), item])
            def names(kind, project=None): return {name} if kind == 'container' else set()
            with self.subTest(mode=mode), mock.patch.dict(own, {'owned_state': lambda path: (path, observer.state)}), \
                    mock.patch.dict(own, {'docker_names': names}), \
                    mock.patch.dict(globals_, {'docker_names': names, 'docker_inspect': inspect}):
                with mock.patch.dict(own, {'docker_inspect': inspect}), \
                        self.assertRaisesRegex(P['WatchError'], 'snapshot_container_(owner|image)_changed'):
                    observer.verify()
                self.assertEqual(inspect.call_count, 2)

    def test_created_not_running_is_recorded_without_stats_exec_or_rss(self):
        observer = self.transition_observer()
        observer.verify = mock.Mock()
        observer.previous_states, observer.previous_vm_cpu, observer.vm_info = {}, None, {}
        def item(role):
            return {'Id': 'a'*64 if role == 'wk-app' else 'b'*64, 'Image': observer.image_ids[role],
                    'State': {'Running': False, 'Status': 'created', 'OOMKilled': False, 'ExitCode': 0,
                              'StartedAt': '0001-01-01T00:00:00Z', 'FinishedAt': '0001-01-01T00:00:00Z'},
                    'HostConfig': {'Memory': 4*1024**3, 'MemorySwap': 4*1024**3, 'NanoCpus': 1000000000}}
        observer.container = lambda role: item(role)
        observer.command = mock.Mock(side_effect=AssertionError('created container must not run a probe'))
        sample = observer.sample()
        observer.command.assert_not_called()
        self.assertEqual(sample['roles']['wk-app']['status'], 'created')
        self.assertIsNone(sample['roles']['wk-app']['docker_stats'])
        self.assertEqual(sample['roles']['wk-app']['proc_sample']['availability'], 'container_stopped_no_historical_rss')
        self.assertIsNone(sample['vm_observation'])

    def test_failure_diagnostics_hash_untrusted_stderr_without_logging_it(self):
        observer = self.transition_observer()
        secret = 'untrusted-credential-or-body-marker'
        failure = P['docker_failure_facts'](['docker', 'container', 'inspect',
            observer.state['project'] + '-wk-app-99'], 1, secret, observer.state, 'owner_resource_verification')
        text = json.dumps(failure)
        self.assertNotIn(secret, text)
        self.assertEqual(failure['stderr_bytes'], len(secret))
        self.assertEqual(len(failure['stderr_sha256']), 64)
        self.assertFalse(failure['raw_stdout_or_inspection_or_environment_recorded'])
        hidden = P['docker_failure_facts'](['docker', 'exec', 'a'*64, 'sh', '-c', secret],
            1, secret, observer.state, 'observer_readonly_command')
        self.assertIsNone(hidden['command_argv'])
        self.assertNotIn(secret, json.dumps(hidden))

    def test_failed_snapshot_leaves_private_diagnostic_and_removes_only_own_marker(self):
        observer = self.transition_observer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); output = root/'output'; output.mkdir(mode=0o700)
            observer.output, observer.marker = output, root/'resource-watch.json'
            observer.duration, observer.interval, observer.stop_on_app_exit = 20, 5, False
            observer.signal_number = None; observer.stop = threading.Event(); observer.previous_states = {}
            def failed():
                observer.last_docker_failure = P['docker_failure_facts'](
                    self.missing_oneoff(observer).cmd, 1, 'Cannot connect to the Docker daemon',
                    observer.state, 'owner_resource_verification')
                raise P['WatchError']('owner_readonly_docker_command_failed')
            observer.sample = failed
            with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(observer.run(), 1)
            metadata = json.loads((output/'metadata.json').read_text())
            self.assertEqual(metadata['sample_count'], 0)
            self.assertIsNone(metadata['sampled_max_weknora_rss_bytes'])
            self.assertEqual(metadata['captured_container_ids'], {})
            self.assertEqual(metadata['last_docker_command_failure']['stderr_summary'], 'docker_daemon_unavailable')
            self.assertTrue(metadata['own_marker_removed'])
            self.assertFalse(metadata['business_quiescence_or_success_attested'])
            self.assertFalse(observer.marker.exists())

    def test_docker_working_set_units_and_cpu(self):
        value = P["stats_value"]({"ID": "a" * 12, "MemUsage": "1.5GiB / 4GiB", "CPUPerc": "103.25%"}, "a" * 64)
        self.assertEqual(value["working_set_bytes_rounded"], 1610612736)
        self.assertEqual(value["cpu_percent_of_one_core"], 103.25)
        self.assertEqual(P["size_bytes"]("128KiB"), 131072)
        self.assertEqual(P["size_bytes"]("1MB"), 1000000)
        for invalid in ("nanGiB", "-1MiB", "1MiB / 2MiB", "3XB"):
            with self.assertRaises(P["WatchError"]):
                P["size_bytes"](invalid)

    def test_stats_rejects_other_ids_and_invalid_cpu(self):
        row = {"ID": "a" * 12, "MemUsage": "1MiB / 4GiB", "CPUPerc": "NaN%"}
        with self.assertRaises(P["WatchError"]):
            P["stats_value"](row, "a" * 64)
        row["CPUPerc"] = "0%"
        with self.assertRaises(P["WatchError"]):
            P["stats_value"](row, "b" * 64)

    def test_shell_reads_only_exact_weknora_comm_not_exec_rss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc, cgroup = root / "proc", root / "cgroup"
            proc.mkdir(); cgroup.mkdir()
            for pid, comm, rss in (("17", "WeKnora", 20), ("18", "sh", 9000), ("19", "WeKnora-helper", 8000)):
                path = proc / pid; path.mkdir()
                (path / "comm").write_text(comm + "\n")
                (path / "status").write_text(f"Name:\t{comm}\nPid:\t{pid}\nVmRSS:\t{rss} kB\nVmHWM:\t30 kB\nThreads:\t4\n")
                (path / "stat").write_text(" ".join([pid, "(" + comm + ")", *("1" for _ in range(20))]) + "\n")
            (proc / "meminfo").write_text("MemTotal: 100 kB\nMemAvailable: 60 kB\nSecretIgnored: 900 kB\n")
            (proc / "stat").write_text("cpu 1 2 3 4 5 6 7 8 9 10\ncpu0 1 1\n")
            (cgroup / "memory.events").write_text("low 0\nmax 1\noom 2\noom_kill 1\n")
            result = subprocess.run(["/bin/sh", "-c", P["probe_script"](proc, cgroup)], capture_output=True, check=True)
            parsed = P["parse_probe"](result.stdout)
            self.assertEqual(parsed["weknora_process_count"], 1)
            self.assertEqual(parsed["weknora_rss_sum_bytes"], 20 * 1024)
            self.assertEqual(parsed["weknora_processes"][0]["pid"], 17)
            self.assertEqual(parsed["cgroup_memory_events"]["oom_kill"], 1)
            self.assertEqual(parsed["vm_memory"], {"MemTotal_bytes": 102400, "MemAvailable_bytes": 61440})

    def test_process_count_duplicate_name_and_missing_rss_guards(self):
        good = b"PROCESS\t17\tWeKnora\tNA\t30\t4\t12\nCGROUP_UNAVAILABLE\n"
        value = P["parse_probe"](good)
        self.assertIsNone(value["weknora_rss_sum_bytes"])
        self.assertIsNone(P["parse_probe"](b"CGROUP_UNAVAILABLE\n")["weknora_rss_sum_bytes"])
        with self.assertRaises(P["WatchError"]):
            P["parse_probe"](good + good)
        with self.assertRaises(P["WatchError"]):
            P["parse_probe"](b"PROCESS\t17\tsh\t9999\t9999\t4\t12\n")
        with self.assertRaises(P["WatchError"]):
            P["parse_probe"](b"CGROUP\toom_kill\tnot-a-counter\n")

    def test_exact_owned_container_image_and_id_guards(self):
        state = {"project": "nc-synldap-abcdef12", "owner_token": "o" * 32}
        item = {"Id": "a" * 64, "Image": "sha256:" + "c" * 64,
                "Name": "/nc-synldap-abcdef12-wk-app-1", "Config": {"Labels": {
                    P["OWNER"]["OWNER_LABEL"]: state["owner_token"],
                    "com.docker.compose.project": state["project"], "com.docker.compose.service": "wk-app"}}}
        self.assertEqual(P["validate_container"](item, state, "wk-app", item["Image"]), item["Id"])
        for change in ({"Image": "sha256:" + "d" * 64}, {"Id": "b" * 64}, {"Name": "/foreign-wk-app-1"}):
            altered = {**item, **change}
            with self.assertRaises(P["WatchError"]):
                P["validate_container"](altered, state, "wk-app", item["Image"], item["Id"])
        altered = {**item, "Config": {"Labels": {**item["Config"]["Labels"], P["OWNER"]["OWNER_LABEL"]: "foreign"}}}
        with self.assertRaises(P["WatchError"]):
            P["validate_container"](altered, state, "wk-app", item["Image"])

    def test_changed_container_rejected_before_process_exec(self):
        observer = object.__new__(P["Observer"])
        observer.state = {"project": "nc-synldap-abcdef12", "owner_token": "o" * 32}
        observer.ids, observer.image_ids = {"wk-app": "a" * 64}, {"wk-app": "sha256:" + "c" * 64}
        item = {"Id": "b" * 64, "Image": observer.image_ids["wk-app"],
                "Name": "/nc-synldap-abcdef12-wk-app-1", "Config": {"Labels": {
                    P["OWNER"]["OWNER_LABEL"]: observer.state["owner_token"],
                    "com.docker.compose.project": observer.state["project"], "com.docker.compose.service": "wk-app"}}}
        globals_ = P["Observer"].container.__globals__
        completed = mock.Mock(returncode=0, stdout=json.dumps([item]).encode())
        with mock.patch.object(globals_["subprocess"], "run", return_value=completed) as called:
            with self.assertRaisesRegex(P["WatchError"], "container_id_changed"):
                observer.container("wk-app")
            self.assertEqual(called.call_count, 1)
            self.assertEqual(called.call_args.args[0][:3], ["docker", "inspect", "--type=container"])
            self.assertNotIn("exec", called.call_args.args[0])

    def test_marker_foreign_replacement_is_never_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "resource-watch.json"
            raw = P["exclusive_json"](path, {"pid": 7, "watcher_id": "mine"})
            identity = (path.stat().st_dev, path.stat().st_ino)
            with self.assertRaises(FileExistsError):
                P["exclusive_json"](path, {})
            path.unlink()
            P["exclusive_json"](path, {"pid": 8, "watcher_id": "other"})
            self.assertFalse(P["remove_own_marker"](path, raw, identity))
            self.assertTrue(path.exists())

    def test_marker_owner_only_and_own_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "resource-watch.json"
            raw = P["exclusive_json"](path, {"pid": 7})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            identity = (path.stat().st_dev, path.stat().st_ino)
            self.assertTrue(P["remove_own_marker"](path, raw, identity))
            self.assertFalse(path.exists())

    def test_private_rejects_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "file"; P["exclusive_json"](path, {})
            link = root / "link"; link.symlink_to(path)
            with self.assertRaises(P["WatchError"]):
                P["private"](link)
            os.link(path, root / "hard")
            with self.assertRaises(P["WatchError"]):
                P["private"](path)

    def test_vm_cpu_excludes_duplicate_guest_ticks(self):
        self.assertEqual(P["vm_cpu_percent"]([0] * 10, [10, 0, 0, 10, 0, 0, 0, 0, 10, 0]), 50)
        self.assertIsNone(P["vm_cpu_percent"](None, [1] * 10))
        self.assertIsNone(P["vm_cpu_percent"]([10] * 10, [1] * 10))

    def test_stopped_app_has_explicit_terminal_and_no_fabricated_rss(self):
        observer = object.__new__(P["Observer"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); output = root / "output"; output.mkdir(mode=0o700)
            observer.output, observer.marker = output, root / "resource-watch.json"
            observer.state = {"project": "nc-synldap-abcdef12"}; observer.image_ids = {}; observer.ids = {}
            observer.duration, observer.interval, observer.stop_on_app_exit = 20, 5, True
            observer.signal_number = None; observer.stop = threading.Event()
            observer.previous_states = {"wk-app": {"running": False, "exit_code": 137}}
            observer.sample = lambda: {"roles": {"wk-app": {"running": False,
                "proc_sample": {"availability": "container_stopped_no_historical_rss"}}}}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(observer.run(), 0)
            metadata = json.loads((output / "metadata.json").read_text())
            self.assertEqual(metadata["terminal_reason"], "app_exit_observed")
            self.assertEqual(metadata["sample_count"], 1)
            self.assertEqual(metadata["observed_application_state"]["exit_code"], 137)
            self.assertIsNone(metadata["sampled_max_weknora_rss_bytes"])
            self.assertFalse(metadata["business_quiescence_or_success_attested"])
            self.assertFalse(observer.marker.exists())
            self.assertFalse((root / "active-probe-workers.json").exists())


if __name__ == "__main__":
    unittest.main()
