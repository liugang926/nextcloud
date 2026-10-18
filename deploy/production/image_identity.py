"""Verify CI config IDs against classic Docker and containerd image IDs.

Docker's classic image store exposes the config digest as ``Id``. The
containerd store can expose an OCI manifest/index digest instead. In that
case only the local content-addressed graph establishes the config identity;
an image tag is never evidence of a match. This module performs no writes.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time


DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
CTR = '/usr/bin/ctr'
CONTAINERD_SOCKETS = ('/run/containerd/containerd.sock',
                      '/var/run/docker/containerd/containerd.sock')
MAX_BLOB = 4 * 1024 * 1024
MAX_DESCRIPTORS = 16
MAX_DEPTH = 2
CONTENT_TIMEOUT = 30
MANIFEST_TYPES = {'application/vnd.oci.image.manifest.v1+json',
                  'application/vnd.docker.distribution.manifest.v2+json'}
INDEX_TYPES = {'application/vnd.oci.image.index.v1+json',
               'application/vnd.docker.distribution.manifest.list.v2+json'}
CONFIG_TYPES = {'application/vnd.oci.image.config.v1+json',
                'application/vnd.docker.container.image.v1+json'}


class IdentityError(Exception):
    """Image identity could not be established; callers must fail closed."""


class ConfigMismatch(IdentityError):
    """A valid local linux/amd64 image has another config digest.

    A caller using a mutable tag to locate a candidate may treat this one
    case as a missing requested image and import its verified archive.
    """


def _require(condition, message):
    if not condition:
        raise IdentityError(message)


def _digest(value):
    _require(isinstance(value, str) and DIGEST.fullmatch(value),
             'Invalid sha256 image content digest')
    return value


def _socket_path():
    for candidate in CONTAINERD_SOCKETS:
        try:
            if Path(candidate).is_socket():
                return candidate
        except OSError as exc:
            raise IdentityError('Cannot inspect local containerd socket') from exc
    raise IdentityError('No supported local containerd socket is available')


def read_content(digest):
    """Read one bounded blob from Docker's local ``moby`` namespace.

    The executable, socket allowlist, namespace and command are fixed.
    There is no registry access, shell invocation or containerd mutation.
    """
    _digest(digest)
    command = [CTR, '--address', _socket_path(), '--namespace', 'moby',
               'content', 'get', digest]
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   close_fds=True)
        deadline = time.monotonic() + CONTENT_TIMEOUT
        result = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                _require(remaining > 0, 'Local containerd content read timed out')
                events = selector.select(remaining)
                _require(events, 'Local containerd content read timed out')
                chunk = os.read(process.stdout.fileno(), min(65536, MAX_BLOB + 1 - len(result)))
                if not chunk:
                    selector.unregister(process.stdout)
                    break
                result.extend(chunk)
                _require(len(result) <= MAX_BLOB, 'Local image content exceeds byte limit')
        remaining = deadline - time.monotonic()
        _require(remaining > 0, 'Local containerd content read timed out')
        _require(process.wait(timeout=remaining) == 0, 'Local containerd content read failed')
        return bytes(result)
    except (OSError, subprocess.SubprocessError) as exc:
        raise IdentityError('Local containerd content read failed') from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()


def _json_blob(data, digest, size=None):
    _require(isinstance(data, bytes) and 0 < len(data) <= MAX_BLOB,
             'Invalid or oversized image content')
    _require(size is None or len(data) == size, 'Image descriptor size mismatch')
    _require('sha256:' + hashlib.sha256(data).hexdigest() == digest,
             'Image content sha256 mismatch')

    def unique_keys(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, 'Duplicate image JSON property')
            value[key] = item
        return value

    try:
        value = json.loads(data, object_pairs_hook=unique_keys)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise IdentityError('Invalid image content JSON') from exc
    _require(isinstance(value, dict), 'Image content must be a JSON object')
    return value


def _descriptor(value, allowed_types):
    _require(isinstance(value, dict), 'Missing image content descriptor')
    digest = _digest(value.get('digest'))
    size = value.get('size')
    _require(type(size) is int and 0 < size <= MAX_BLOB,
             'Invalid or oversized image descriptor size')
    media_type = value.get('mediaType')
    _require(isinstance(media_type, str) and media_type in allowed_types,
             'Unsupported image descriptor media type')
    return digest, size, media_type


def _platform(value):
    _require(isinstance(value, dict), 'Invalid image platform')
    _require(isinstance(value.get('os'), str) and isinstance(value.get('architecture'), str),
             'Incomplete image platform')
    return value['os'], value['architecture']


def verify_image(expected, inspected, *, content_reader=None):
    """Return Docker's runtime ID after proving the requested config identity.

    ``expected`` is a release image entry with ``id`` and ``platform``;
    ``inspected`` is one result from ``docker image inspect``. Missing-image
    detection belongs to the caller. Every validation/read error raises
    ``IdentityError``; a fully verified alternative OCI config raises its
    ``ConfigMismatch`` subclass. ``content_reader`` accepts a digest and
    returns bytes, allowing the verification graph to be tested offline.
    """
    _require(isinstance(expected, dict) and expected.get('platform') == 'linux/amd64',
             'Only release images for linux/amd64 are supported')
    expected_id = _digest(expected.get('id'))
    _require(isinstance(inspected, dict), 'Invalid Docker image inspection')
    runtime_id = _digest(inspected.get('Id'))
    _require((inspected.get('Os'), inspected.get('Architecture')) == ('linux', 'amd64'),
             'Docker image is not linux/amd64')
    # Docker classic's image ID is already the exact config digest.
    if runtime_id == expected_id and inspected.get('Descriptor') is None:
        return runtime_id

    reader = content_reader if content_reader is not None else read_content
    descriptor = inspected.get('Descriptor')
    root_size, root_type = None, None
    if descriptor is not None:
        root_digest, root_size, root_type = _descriptor(descriptor, MANIFEST_TYPES | INDEX_TYPES)
        _require(root_digest == runtime_id, 'Docker image descriptor/ID mismatch')

    descriptor_count = 0
    visited = set()

    def load(digest, size=None):
        try:
            data = reader(digest)
        except IdentityError:
            raise
        except Exception as exc:
            raise IdentityError('Cannot read local image content') from exc
        return _json_blob(data, digest, size)

    def walk(digest, size=None, declared_type=None, depth=0):
        nonlocal descriptor_count
        _require(depth <= MAX_DEPTH, 'Image index nesting exceeds depth limit')
        _require(digest not in visited, 'Repeated or cyclic image content descriptor')
        visited.add(digest)
        value = load(digest, size)
        _require(type(value.get('schemaVersion')) is int and value['schemaVersion'] == 2,
                 'Unsupported image manifest schema')
        media_type = value.get('mediaType', declared_type)
        if media_type is None:
            # OCI's JSON mediaType is optional; structural inference is only
            # allowed when exactly one manifest shape is present.
            if 'manifests' in value and 'config' not in value:
                media_type = 'application/vnd.oci.image.index.v1+json'
            elif 'config' in value and 'manifests' not in value:
                media_type = 'application/vnd.oci.image.manifest.v1+json'
        _require(isinstance(media_type, str) and media_type in MANIFEST_TYPES | INDEX_TYPES,
                 'Unsupported image manifest media type')
        _require(declared_type is None or media_type == declared_type,
                 'Image descriptor/content media type mismatch')
        if media_type in MANIFEST_TYPES:
            _require('manifests' not in value and isinstance(value.get('layers'), list),
                     'Invalid image manifest structure')
            config_id, config_size, _ = _descriptor(value.get('config'), CONFIG_TYPES)
            config = load(config_id, config_size)
            _require(_platform(config) == ('linux', 'amd64'),
                     'Image config is not linux/amd64')
            return config_id

        _require('config' not in value and isinstance(value.get('manifests'), list),
                 'Invalid image index structure')
        children = value['manifests']
        descriptor_count += len(children)
        _require(0 < len(children) and descriptor_count <= MAX_DESCRIPTORS,
                 'Image index exceeds descriptor limit')
        candidates = []
        for child in children:
            child_id, child_size, child_type = _descriptor(child, MANIFEST_TYPES | INDEX_TYPES)
            platform = child.get('platform')
            if platform is None:
                _require(child_type in INDEX_TYPES, 'Image manifest descriptor has no platform')
                candidates.append((child_id, child_size, child_type))
            elif _platform(platform) == ('linux', 'amd64'):
                candidates.append((child_id, child_size, child_type))
        _require(len(candidates) == 1, 'Image index must select one linux/amd64 image')
        return walk(*candidates[0], depth=depth + 1)

    config_id = walk(runtime_id, root_size, root_type)
    if config_id != expected_id:
        raise ConfigMismatch('Verified local image has a different config digest')
    return runtime_id
