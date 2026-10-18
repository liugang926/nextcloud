import pathlib
import re
import os
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

from parallel_download import DownloadError, download_parallel


class Response:
    def __init__(self, body, start, end, total, status=206, content_range=None, read_hook=None):
        self.status = status
        self.headers = {'Content-Range': content_range or f'bytes {start}-{end}/{total}'}
        self.body = body
        self.read_hook = read_hook
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        self.read_limits.append(limit)
        if self.read_hook:
            self.read_hook()
        return self.body[:limit]


class ParallelDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.destination = pathlib.Path(self.tmp.name) / 'images.tar.gz.part00'
        self.url = 'https://example.invalid/immutable-asset?signature=do-not-log'
        self.data = b'abcdefghijklmn'
        self.requests = []
        self.responses = []
        self.lock = threading.Lock()
        sleep_patch = patch('parallel_download.time.sleep')
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def network(self, request, *, timeout):
        self.assertEqual(request.full_url, self.url)
        self.assertEqual(request.get_header('Accept-encoding'), 'identity')
        self.assertEqual(request.get_header('User-agent'), 'nextcloud-production-updater')
        self.assertGreater(timeout, 0)
        match = re.fullmatch(r'bytes=(\d+)-(\d+)', request.get_header('Range'))
        self.assertIsNotNone(match)
        start, end = map(int, match.groups())
        self.assertGreaterEqual(start, 0)
        self.assertLess(end, len(self.data))
        self.assertLessEqual(end - start + 1, 4)
        with self.lock:
            self.requests.append((start, end))
        response = Response(self.data[start:end + 1], start, end, len(self.data))
        response.url = self.url
        with self.lock:
            self.responses.append(response)
        return response

    def test_multiple_chunks_download_concurrently_and_assemble_in_order(self):
        self.data = b'abcdefghijkl'
        barrier = threading.Barrier(3)
        active = 0
        maximum = 0

        def read_hook():
            nonlocal active, maximum
            with self.lock:
                active += 1
                maximum = max(maximum, active)
            try:
                barrier.wait(timeout=5)
            finally:
                with self.lock:
                    active -= 1

        def network(request, *, timeout):
            response = self.network(request, timeout=timeout)
            response.read_hook = read_hook
            return response

        with patch('parallel_download.urllib.request.urlopen', side_effect=network):
            download_parallel(self.url, self.destination, len(self.data), workers=3, chunk_size=4)
        self.assertEqual(maximum, 3)
        self.assertCountEqual(self.requests, [(0, 3), (4, 7), (8, 11)])
        self.assertEqual(self.destination.read_bytes(), self.data)
        self.assertFalse(self.destination.with_name(self.destination.name + '.chunks').exists())
        self.assertTrue(all(response.read_limits == [5] for response in self.responses))

    def test_existing_partial_imports_only_complete_chunks(self):
        self.destination.write_bytes(b'abcdef')
        with patch('parallel_download.urllib.request.urlopen', side_effect=self.network):
            download_parallel(self.url, self.destination, len(self.data), workers=3, chunk_size=4)
        self.assertCountEqual(self.requests, [(4, 7), (8, 11), (12, 13)])
        self.assertEqual(self.destination.read_bytes(), self.data)
        self.assertCountEqual([response.read_limits for response in self.responses], [[5], [5], [3]])

    def test_invalid_status_or_content_range_is_rejected_before_body_read(self):
        self.data = b'abcd'
        for status, header in [(200, 'bytes 0-3/4'), (206, 'bytes 1-3/4'),
                               (206, 'bytes 0-3/999'), (206, 'bytes 0-4/4')]:
            with self.subTest(status=status, header=header):
                responses = []

                def invalid(request, *, timeout):
                    response = Response(self.data, 0, 3, 4, status=status, content_range=header)
                    responses.append(response)
                    return response

                with patch('parallel_download.urllib.request.urlopen', side_effect=invalid) as network:
                    with self.assertRaises(DownloadError):
                        download_parallel(self.url, self.destination, 4, workers=1, chunk_size=4)
                self.assertEqual(network.call_count, 3)
                self.assertTrue(all(not response.read_limits for response in responses))
                self.assertFalse(self.destination.exists())
                self.assertFalse(list(pathlib.Path(self.tmp.name).rglob('*.part')))

    def test_short_or_oversized_body_is_rejected_with_bounded_reads(self):
        self.data = b'abcd'
        for body in [b'abc', b'abcdefghi']:
            with self.subTest(body=body):
                responses = []

                def invalid(request, *, timeout):
                    response = Response(body, 0, 3, 4)
                    responses.append(response)
                    return response

                with patch('parallel_download.urllib.request.urlopen', side_effect=invalid) as network:
                    with self.assertRaisesRegex(DownloadError, 'wrong size'):
                        download_parallel(self.url, self.destination, 4, workers=1, chunk_size=4)
                self.assertEqual(network.call_count, 3)
                self.assertTrue(all(response.read_limits == [5] for response in responses))
                self.assertFalse(self.destination.exists())
                self.assertFalse(list(pathlib.Path(self.tmp.name).rglob('*.part')))

    def test_failure_retains_partial_and_complete_chunks_for_retry(self):
        # Seed one full block and an incomplete second block. A successful
        # first missing block must survive a later block's failed retries.
        self.destination.write_bytes(b'abcdef')

        def fail_last_chunk(request, *, timeout):
            response = self.network(request, timeout=timeout)
            if request.get_header('Range') == 'bytes=12-13':
                response.status = 200
            return response

        with patch('parallel_download.urllib.request.urlopen', side_effect=fail_last_chunk):
            with self.assertRaises(DownloadError):
                download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        self.assertEqual(self.destination.read_bytes(), b'abcdef')
        cached = sorted(pathlib.Path(self.tmp.name).rglob('*.part'))
        self.assertEqual([path.read_bytes() for path in cached], [b'abcd', b'efgh', b'ijkl'])
        self.assertEqual(self.requests.count((12, 13)), 3)
        self.requests.clear()

        with patch('parallel_download.urllib.request.urlopen', side_effect=self.network):
            download_parallel(self.url, self.destination, len(self.data), workers=3, chunk_size=4)
        self.assertEqual(self.requests, [(12, 13)])
        self.assertEqual(self.destination.read_bytes(), self.data)
        self.assertFalse(list(pathlib.Path(self.tmp.name).rglob('.chunk-*')))
        self.assertFalse(list(pathlib.Path(self.tmp.name).glob('*.assembling-*')))

    def test_network_failure_is_sanitized_without_url_or_token(self):
        failure = urllib.error.URLError(self.url)
        with patch('parallel_download.urllib.request.urlopen', side_effect=failure):
            with self.assertRaises(DownloadError) as caught:
                download_parallel(self.url, self.destination, 4, workers=1, chunk_size=4)
        self.assertNotIn(self.url, str(caught.exception))
        self.assertNotIn('do-not-log', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_existing_complete_destination_needs_no_requests(self):
        self.destination.write_bytes(self.data)
        with patch('parallel_download.urllib.request.urlopen') as network:
            download_parallel(self.url, self.destination, len(self.data), chunk_size=4)
        network.assert_not_called()

    def test_invalid_bounds_are_rejected_without_requests(self):
        for options in [dict(expected_size=-1), dict(expected_size=4, workers=0),
                        dict(expected_size=4, chunk_size=0)]:
            with self.subTest(options=options):
                with patch('parallel_download.urllib.request.urlopen') as network:
                    with self.assertRaises(ValueError):
                        download_parallel(self.url, self.destination, **options)
                network.assert_not_called()

    def test_worker_limit_cannot_exceed_eight(self):
        with patch('parallel_download.urllib.request.urlopen') as network:
            with self.assertRaises(ValueError):
                download_parallel(self.url, self.destination, 4, workers=9)
        network.assert_not_called()

    def test_chunk_limit_cannot_exceed_four_mebibytes(self):
        with patch('parallel_download.urllib.request.urlopen') as network:
            with self.assertRaises(ValueError):
                download_parallel(self.url, self.destination, 4, chunk_size=4 * 1024 * 1024 + 1)
        network.assert_not_called()

    def test_boolean_bounds_are_not_integers(self):
        for options in [dict(expected_size=True), dict(expected_size=4, workers=True),
                        dict(expected_size=4, chunk_size=True)]:
            with self.subTest(options=options), patch('parallel_download.urllib.request.urlopen') as network:
                with self.assertRaises(ValueError):
                    download_parallel(self.url, self.destination, **options)
                network.assert_not_called()

    def test_empty_asset_is_created_without_network(self):
        with patch('parallel_download.urllib.request.urlopen') as network:
            download_parallel(self.url, self.destination, 0)
        network.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), b'')
        self.assertFalse(self.destination.with_name(self.destination.name + '.chunks').exists())

    def test_wrong_sized_cached_chunk_is_fetched_again(self):
        def fail_last(request, *, timeout):
            response = self.network(request, timeout=timeout)
            if request.get_header('Range') == 'bytes=12-13':
                response.status = 200
            return response

        with patch('parallel_download.urllib.request.urlopen', side_effect=fail_last):
            with self.assertRaises(DownloadError):
                download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        cached = sorted(pathlib.Path(self.tmp.name).rglob('*.part'))
        cached[1].write_bytes(b'e')
        self.requests.clear()
        with patch('parallel_download.urllib.request.urlopen', side_effect=self.network):
            download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        self.assertEqual(self.requests, [(4, 7), (12, 13)])
        self.assertEqual(self.destination.read_bytes(), self.data)

    def test_oversized_destination_is_replaced_without_importing_prefix(self):
        self.destination.write_bytes(b'not-the-asset-and-too-long')
        with patch('parallel_download.urllib.request.urlopen', side_effect=self.network):
            download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        self.assertEqual(self.requests, [(0, 3), (4, 7), (8, 11), (12, 13)])
        self.assertEqual(self.destination.read_bytes(), self.data)

    def test_different_url_does_not_reuse_old_chunks(self):
        def fail_last(request, *, timeout):
            response = self.network(request, timeout=timeout)
            if request.get_header('Range') == 'bytes=12-13':
                response.status = 200
            return response

        with patch('parallel_download.urllib.request.urlopen', side_effect=fail_last):
            with self.assertRaises(DownloadError):
                download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        self.url = 'https://example.invalid/different-immutable-asset?signature=another-secret'
        self.requests.clear()
        with patch('parallel_download.urllib.request.urlopen', side_effect=self.network):
            download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        self.assertEqual(self.requests, [(0, 3), (4, 7), (8, 11), (12, 13)])
        self.assertEqual(self.destination.read_bytes(), self.data)

    def test_retained_cache_contains_no_url_or_signature_metadata(self):
        def fail_last(request, *, timeout):
            response = self.network(request, timeout=timeout)
            if request.get_header('Range') == 'bytes=12-13':
                response.status = 200
            return response

        with patch('parallel_download.urllib.request.urlopen', side_effect=fail_last):
            with self.assertRaises(DownloadError):
                download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        for path in pathlib.Path(self.tmp.name).rglob('*'):
            self.assertNotIn('do-not-log', str(path))
            self.assertNotIn('signature', str(path))
            if path.is_file():
                self.assertNotIn(self.url.encode(), path.read_bytes())
                self.assertNotIn(b'do-not-log', path.read_bytes())

    def test_transient_request_error_retries_then_succeeds(self):
        self.data = b'abcd'
        attempts = 0

        def transient(request, *, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise urllib.error.URLError(self.url)
            return self.network(request, timeout=timeout)

        with patch('parallel_download.urllib.request.urlopen', side_effect=transient):
            download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        self.assertEqual(attempts, 2)
        self.assertEqual(self.destination.read_bytes(), self.data)

    def test_untrusted_redirect_is_rejected_before_reading_body(self):
        self.data = b'abcd'

        def untrusted(request, *, timeout):
            response = self.network(request, timeout=timeout)
            response.url = 'https://attacker.invalid/asset?token=do-not-log'
            return response

        with patch('parallel_download.urllib.request.urlopen', side_effect=untrusted):
            with self.assertRaises(DownloadError) as caught:
                download_parallel(self.url, self.destination, 4, workers=1, chunk_size=4,
                                  allowed_hosts={'example.invalid'})
        self.assertTrue(all(not response.read_limits for response in self.responses))
        self.assertNotIn('attacker', str(caught.exception))
        self.assertNotIn('do-not-log', str(caught.exception))
        self.assertFalse(self.destination.exists())

    def test_allowed_https_redirect_is_accepted(self):
        def trusted(request, *, timeout):
            response = self.network(request, timeout=timeout)
            response.url = 'https://release-assets.githubusercontent.com/asset?token=do-not-log'
            return response

        with patch('parallel_download.urllib.request.urlopen', side_effect=trusted):
            download_parallel(self.url, self.destination, len(self.data), workers=3, chunk_size=4,
                              allowed_hosts={'release-assets.githubusercontent.com'})
        self.assertEqual(self.destination.read_bytes(), self.data)

    def test_https_downgrade_is_rejected_before_reading_body(self):
        self.data = b'abcd'

        def downgraded(request, *, timeout):
            response = self.network(request, timeout=timeout)
            response.url = 'http://example.invalid/asset'
            return response

        with patch('parallel_download.urllib.request.urlopen', side_effect=downgraded):
            with self.assertRaises(DownloadError):
                download_parallel(self.url, self.destination, 4, workers=1, chunk_size=4,
                                  allowed_hosts={'example.invalid'})
        self.assertTrue(all(not response.read_limits for response in self.responses))

    def test_chunk_write_failure_keeps_existing_partial_and_cleans_temporary_files(self):
        self.destination.write_bytes(b'abc')
        with patch('parallel_download.urllib.request.urlopen', side_effect=self.network), \
                patch('parallel_download.os.replace', side_effect=OSError('local disk failure')):
            with self.assertRaises(DownloadError):
                download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        self.assertEqual(self.destination.read_bytes(), b'abc')
        self.assertFalse(list(pathlib.Path(self.tmp.name).rglob('.chunk-*')))
        self.assertFalse(list(pathlib.Path(self.tmp.name).rglob('*.part')))

    def test_assembly_replace_failure_keeps_chunks_for_retry(self):
        self.destination.write_bytes(b'abc')
        replace = os.replace

        def fail_destination(source, destination):
            if pathlib.Path(destination) == self.destination:
                raise OSError('local disk failure')
            return replace(source, destination)

        with patch('parallel_download.urllib.request.urlopen', side_effect=self.network), \
                patch('parallel_download.os.replace', side_effect=fail_destination):
            with self.assertRaises(OSError):
                download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        self.assertEqual(self.destination.read_bytes(), b'abc')
        self.assertEqual(len(list(pathlib.Path(self.tmp.name).rglob('*.part'))), 4)
        self.assertFalse(list(pathlib.Path(self.tmp.name).glob('*.assembling-*')))
        with patch('parallel_download.urllib.request.urlopen') as network:
            download_parallel(self.url, self.destination, len(self.data), workers=1, chunk_size=4)
        network.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), self.data)


if __name__ == '__main__':
    unittest.main()
