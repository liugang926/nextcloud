#!/usr/bin/env python3
"""Offline config/manifest identity and bounded local-reader tests."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('image_identity', HERE / 'image_identity.py')
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
OCI_MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
OCI_INDEX = 'application/vnd.oci.image.index.v1+json'
OCI_CONFIG = 'application/vnd.oci.image.config.v1+json'


class Graph:
    def __init__(self, architecture='amd64', os='linux'):
        self.blobs = {}
        self.config = self.add({'architecture': architecture, 'os': os,
                                'rootfs': {'type': 'layers', 'diff_ids': []}})
        self.manifest = self.add({'schemaVersion': 2, 'mediaType': OCI_MANIFEST,
                                  'config': self.descriptor(self.config, OCI_CONFIG),
                                  'layers': []})

    def add(self, value):
        data = json.dumps(value, sort_keys=True).encode()
        digest = 'sha256:' + hashlib.sha256(data).hexdigest()
        self.blobs[digest] = data
        return digest

    def descriptor(self, digest, media_type, platform=None):
        value = {'digest': digest, 'size': len(self.blobs[digest]), 'mediaType': media_type}
        if platform is not None:
            value['platform'] = platform
        return value

    def index(self, children=None):
        return self.add({'schemaVersion': 2, 'mediaType': OCI_INDEX,
                         'manifests': children if children is not None else [
                             self.descriptor(self.manifest, OCI_MANIFEST,
                                             {'os': 'linux', 'architecture': 'amd64'})]})

    def expected(self):
        return {'id': self.config, 'platform': 'linux/amd64', 'source': 'example:mutable'}

    def inspected(self, runtime_id=None, descriptor=False):
        runtime_id = runtime_id or self.manifest
        value = {'Id': runtime_id, 'Os': 'linux', 'Architecture': 'amd64'}
        if descriptor:
            media_type = json.loads(self.blobs[runtime_id])['mediaType']
            value['Descriptor'] = self.descriptor(runtime_id, media_type)
        return value

    def verify(self, runtime_id=None, expected=None, inspected=None, reader=None):
        return identity.verify_image(expected if expected is not None else self.expected(),
                                     inspected if inspected is not None else self.inspected(runtime_id),
                                     content_reader=reader or self.blobs.__getitem__)


class VerificationTests(unittest.TestCase):
    def test_classic_config_id_matches_without_any_content_access(self):
        graph = Graph()
        reader = mock.Mock(side_effect=AssertionError('classic store must not use ctr'))
        self.assertEqual(graph.verify(inspected=graph.inspected(graph.config), reader=reader), graph.config)
        reader.assert_not_called()

    def test_oci_manifest_links_runtime_digest_to_expected_config(self):
        graph = Graph()
        self.assertNotEqual(graph.manifest, graph.config)
        self.assertEqual(graph.verify(inspected=graph.inspected(descriptor=True)), graph.manifest)

    def test_explicit_oci_descriptor_cannot_bypass_config_proof_on_equal_ids(self):
        graph = Graph()
        expected = graph.expected()
        expected['id'] = graph.manifest
        with self.assertRaises(identity.ConfigMismatch):
            graph.verify(expected=expected, inspected=graph.inspected(descriptor=True))

    def test_docker_schema2_manifest_and_config_are_supported(self):
        graph = Graph()
        value = json.loads(graph.blobs[graph.manifest])
        value['mediaType'] = 'application/vnd.docker.distribution.manifest.v2+json'
        value['config']['mediaType'] = 'application/vnd.docker.container.image.v1+json'
        manifest = graph.add(value)
        self.assertEqual(graph.verify(manifest), manifest)

    def test_optional_body_media_type_can_be_inferred(self):
        graph = Graph()
        value = json.loads(graph.blobs[graph.manifest])
        del value['mediaType']
        manifest = graph.add(value)
        self.assertEqual(graph.verify(manifest), manifest)

    def test_index_selects_amd64_and_returns_pinned_runtime_index_id(self):
        graph = Graph()
        wrong_arch = Graph('arm64')
        graph.blobs.update(wrong_arch.blobs)
        index = graph.index([
            graph.descriptor(wrong_arch.manifest, OCI_MANIFEST, {'os': 'linux', 'architecture': 'arm64'}),
            graph.descriptor(graph.manifest, OCI_MANIFEST, {'os': 'linux', 'architecture': 'amd64'})])
        reader = mock.Mock(side_effect=graph.blobs.__getitem__)
        self.assertEqual(graph.verify(index, reader=reader), index)
        self.assertNotIn(mock.call(wrong_arch.manifest), reader.call_args_list)

    def test_nested_index_without_platform_selects_bounded_child(self):
        graph = Graph()
        inner = graph.index()
        outer = graph.index([graph.descriptor(inner, OCI_INDEX)])
        self.assertEqual(graph.verify(outer), outer)

    def test_only_fully_verified_other_config_is_a_config_mismatch(self):
        graph = Graph()
        expected = graph.expected()
        expected['id'] = 'sha256:' + 'f' * 64
        with self.assertRaises(identity.ConfigMismatch):
            graph.verify(expected=expected)
        graph.blobs[graph.config] += b' '
        with self.assertRaises(identity.IdentityError) as result:
            graph.verify(expected=expected)
        self.assertNotIsInstance(result.exception, identity.ConfigMismatch)

    def test_rejects_runtime_or_config_blob_hash_corruption(self):
        for kind in ('manifest', 'config'):
            with self.subTest(kind=kind):
                graph = Graph()
                digest = getattr(graph, kind)
                original = graph.blobs[digest]
                graph.blobs[digest] = original.replace(b'linux', b'other') if kind == 'config' else original + b' '
                with self.assertRaises(identity.IdentityError):
                    graph.verify()

    def test_rejects_inspected_or_config_architecture_mismatch(self):
        for source in ('inspection', 'config', 'config_os'):
            with self.subTest(source=source):
                graph = Graph(architecture='arm64' if source == 'config' else 'amd64',
                              os='windows' if source == 'config_os' else 'linux')
                inspected = graph.inspected()
                if source == 'inspection':
                    inspected['Architecture'] = 'arm64'
                with self.assertRaises(identity.IdentityError) as result:
                    graph.verify(inspected=inspected)
                self.assertNotIsInstance(result.exception, identity.ConfigMismatch)

    def test_rejects_descriptor_platform_lie(self):
        graph = Graph('arm64')
        index = graph.index()
        with self.assertRaises(identity.IdentityError):
            graph.verify(index)

    def test_rejects_wrong_config_size_child_size_or_root_descriptor(self):
        for source in ('config', 'child', 'root_digest', 'root_size', 'root_media_type'):
            with self.subTest(source=source):
                graph = Graph()
                if source == 'config':
                    value = json.loads(graph.blobs[graph.manifest])
                    value['config']['size'] += 1
                    inspected = graph.inspected(graph.add(value))
                elif source == 'child':
                    child = graph.descriptor(graph.manifest, OCI_MANIFEST,
                                             {'os': 'linux', 'architecture': 'amd64'})
                    child['size'] += 1
                    inspected = graph.inspected(graph.index([child]))
                else:
                    inspected = graph.inspected(descriptor=True)
                    if source == 'root_digest':
                        inspected['Descriptor']['digest'] = 'sha256:' + 'f' * 64
                    elif source == 'root_size':
                        inspected['Descriptor']['size'] += 1
                    else:
                        inspected['Descriptor']['mediaType'] = OCI_INDEX
                with self.assertRaises(identity.IdentityError):
                    graph.verify(inspected=inspected)

    def test_rejects_ambiguous_index_and_index_without_matching_platform(self):
        for platforms in (['amd64', 'amd64'], ['arm64'], []):
            with self.subTest(platforms=platforms):
                graph = Graph()
                index = graph.index([graph.descriptor(graph.manifest, OCI_MANIFEST,
                                                       {'os': 'linux', 'architecture': arch})
                                     for arch in platforms])
                with self.assertRaises(identity.IdentityError):
                    graph.verify(index)

    def test_rejects_manifest_descriptor_without_platform(self):
        graph = Graph()
        index = graph.index([graph.descriptor(graph.manifest, OCI_MANIFEST)])
        with self.assertRaises(identity.IdentityError):
            graph.verify(index)

    def test_index_descriptor_limit_counts_all_branches(self):
        graph = Graph()
        index = graph.index([graph.descriptor(graph.manifest, OCI_MANIFEST,
                                               {'os': 'linux', 'architecture': 'arm64'})] * 16 + [
            graph.descriptor(graph.manifest, OCI_MANIFEST, {'os': 'linux', 'architecture': 'amd64'})])
        with self.assertRaises(identity.IdentityError):
            graph.verify(index)

    def test_nested_indexes_cannot_exceed_depth_limit(self):
        graph = Graph()
        index = graph.index()
        for _ in range(2):
            index = graph.index([graph.descriptor(index, OCI_INDEX)])
        with self.assertRaises(identity.IdentityError):
            graph.verify(index)

    def test_blob_limit_applies_even_to_injected_readers(self):
        graph = Graph()
        with mock.patch.object(identity, 'MAX_BLOB', len(graph.blobs[graph.manifest]) - 1):
            with self.assertRaises(identity.IdentityError):
                graph.verify()

    def test_rejects_duplicate_json_keys_and_nonobject_content(self):
        for data in (b'{"schemaVersion":2,"schemaVersion":2}', b'[]', b'not json'):
            with self.subTest(data=data):
                graph = Graph()
                digest = 'sha256:' + hashlib.sha256(data).hexdigest()
                graph.blobs[digest] = data
                with self.assertRaises(identity.IdentityError):
                    graph.verify(digest)

    def test_rejects_unsupported_schema_media_types_and_descriptor_sizes(self):
        for field, value in (('schemaVersion', True), ('mediaType', 'application/octet-stream'),
                             ('mediaType', []),
                             ('config.size', True), ('config.size', 0),
                             ('config.mediaType', 'unknown/config'), ('config.mediaType', [])):
            with self.subTest(field=field, value=value):
                graph = Graph()
                manifest = json.loads(graph.blobs[graph.manifest])
                if field.startswith('config.'):
                    manifest['config'][field.split('.')[1]] = value
                else:
                    manifest[field] = value
                with self.assertRaises(identity.IdentityError):
                    graph.verify(graph.add(manifest))

    def test_invalid_or_missing_inputs_fail_as_identity_errors(self):
        graph = Graph()
        for expected, inspected in ((None, graph.inspected()), ({}, graph.inspected()),
                                     (graph.expected(), None), (graph.expected(), {}),
                                     ({'id': '--help', 'platform': 'linux/amd64'}, graph.inspected())):
            with self.subTest(expected=expected, inspected=inspected):
                with self.assertRaises(identity.IdentityError):
                    identity.verify_image(expected, inspected, content_reader=graph.blobs.__getitem__)

    def test_content_access_failure_is_never_config_mismatch_or_missing(self):
        graph = Graph()
        for error in (OSError('socket unavailable'), KeyError(graph.manifest), identity.IdentityError('ctr failed')):
            with self.subTest(error=error):
                reader = mock.Mock(side_effect=error)
                with self.assertRaises(identity.IdentityError) as result:
                    graph.verify(reader=reader)
                self.assertNotIsInstance(result.exception, identity.ConfigMismatch)


class ContentReaderTests(unittest.TestCase):
    def process(self, code):
        """Exercise the real bounded pipe reader with a harmless local child."""
        real_popen = subprocess.Popen
        calls = []

        def start(command, **kwargs):
            calls.append((command, kwargs))
            return real_popen([sys.executable, '-c', code], **kwargs)

        return calls, mock.patch.object(identity.subprocess, 'Popen', side_effect=start)

    def test_fixed_executable_socket_namespace_and_readonly_command(self):
        calls, process = self.process('import sys; sys.stdout.buffer.write(b"content")')
        digest = 'sha256:' + 'a' * 64
        with mock.patch.object(identity, '_socket_path', return_value='/run/containerd/containerd.sock'), process:
            self.assertEqual(identity.read_content(digest), b'content')
        self.assertEqual(calls[0][0], ['/usr/bin/ctr', '--address', '/run/containerd/containerd.sock',
                                      '--namespace', 'moby', 'content', 'get', digest])
        self.assertNotIn('shell', calls[0][1])
        self.assertEqual(calls[0][1]['stdin'], subprocess.DEVNULL)

    def test_invalid_digest_cannot_become_a_command_argument(self):
        with mock.patch.object(identity.subprocess, 'Popen') as process:
            with self.assertRaises(identity.IdentityError):
                identity.read_content('--help')
        process.assert_not_called()

    def test_missing_socket_and_missing_ctr_fail_closed(self):
        with mock.patch.object(identity.Path, 'is_socket', return_value=False):
            with self.assertRaises(identity.IdentityError):
                identity.read_content('sha256:' + 'a' * 64)
        with mock.patch.object(identity, '_socket_path', return_value='/run/containerd/containerd.sock'), \
                mock.patch.object(identity.subprocess, 'Popen', side_effect=FileNotFoundError('no ctr')):
            with self.assertRaises(identity.IdentityError):
                identity.read_content('sha256:' + 'a' * 64)

    def test_socket_selection_only_uses_fixed_known_paths(self):
        examined = []

        def is_socket(path):
            examined.append(str(path))
            return str(path) == identity.CONTAINERD_SOCKETS[-1]

        with mock.patch.object(identity.Path, 'is_socket', new=is_socket):
            self.assertEqual(identity._socket_path(), identity.CONTAINERD_SOCKETS[-1])
        self.assertEqual(examined, list(identity.CONTAINERD_SOCKETS))

    def test_nonzero_ctr_exit_is_an_error_even_with_valid_stdout(self):
        _, process = self.process('import sys; sys.stdout.buffer.write(b"content"); sys.exit(1)')
        with mock.patch.object(identity, '_socket_path', return_value='/run/containerd/containerd.sock'), process:
            with self.assertRaises(identity.IdentityError):
                identity.read_content('sha256:' + 'a' * 64)

    def test_oversized_ctr_stdout_stops_at_byte_limit(self):
        _, process = self.process('import sys; sys.stdout.buffer.write(b"x" * 1000000)')
        with mock.patch.object(identity, '_socket_path', return_value='/run/containerd/containerd.sock'), \
                mock.patch.object(identity, 'MAX_BLOB', 64), process:
            with self.assertRaisesRegex(identity.IdentityError, 'byte limit'):
                identity.read_content('sha256:' + 'a' * 64)

    def test_stalled_ctr_is_killed_at_timeout(self):
        _, process = self.process('import time; time.sleep(20)')
        with mock.patch.object(identity, '_socket_path', return_value='/run/containerd/containerd.sock'), \
                mock.patch.object(identity, 'CONTENT_TIMEOUT', 0.05), process:
            with self.assertRaisesRegex(identity.IdentityError, 'timed out'):
                identity.read_content('sha256:' + 'a' * 64)


if __name__ == '__main__':
    unittest.main()
