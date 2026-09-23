# Local development compatibility matrix

Observed on 2026-09-23 in the local `linux/arm64` Docker environment. This
records the versions used for development and smoke checks. It is not a
production support or release certification matrix.

## Running Docker stack

The image references below come from `compose.yaml`. The observed runtime
versions came from commands inside the running containers. The Nextcloud and
cron services use the same image. PostgreSQL and Redis have separate Docker
volumes; the application is mounted read-only from this repository.

| Service | Image reference in Compose | Observed runtime |
| --- | --- | --- |
| Nextcloud and cron | `nextcloud:34.0.4-apache@sha256:a5ace30c695afe48c2c406e940ee7886a81e13fa382e57cd68b2416d1a66914c` | Nextcloud `34.0.4` (`occ` version `34.0.4.1`); PHP CLI `8.5.10` |
| PostgreSQL | `postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416` | PostgreSQL `16.13` |
| Redis | `redis:7-alpine@sha256:8b81dd37ff027bec4e516d41acfbe9fe2460070dc6d4a4570a2ac5b9d59df065` | Redis `7.4.8` |
| Mock embedding service | `python:3.12-alpine@sha256:4c47124a8391cb7a9f571164147d154777cf012a4ece5f86097130d7a4478111` | Local test dependency only |

`docker compose ps` reported the Nextcloud, PostgreSQL, Redis and mock
embedding services healthy. The installed and enabled
`integration_weknora` application reported version `0.4.11` after local migration; its `info.xml`
declares Nextcloud major version 34 as its local compatibility range. The
instance's `occ status` reported `maintenance: false` and
`needsDbUpgrade: false`.
The runtime-only `dist/integration_weknora-0.4.11.tar.gz` archive was built
twice with the same SHA-256 checksum
`3731aa8bc1c237e6acc38098df57152116b1bbae7ce5d5be94276b14c68c1d20`.

## Build inputs and paired WeKnora runtime

| Component | Local input | Observed version or image ID |
| --- | --- | --- |
| Nextcloud app frontend | Host `node` and `npm`; CI requests Node major 24 in `.github/workflows/integration.yml` | Node `v24.14.0`; npm `11.9.0`; `package-lock.json` is used by `npm ci` |
| WeKnora Go build and test | Local `weknora-go-test:1.26-sqlite` image | Go `1.26.8 linux/arm64`; local image ID `sha256:5a74e8b5137496d3e22c72c01d862c4cbb1fe6ce476ddf5616fd9a807d6ae9ac` |
| WeKnora runtime base | Local `weknora-ldap-app:final` image | Local image ID `sha256:36667b698350db76c044e797d3c92e98f8acb146750e30401a2dd6a61a6b6c79` |
| Patched WeKnora runtime | Local `weknora-ldap-app:nextcloud-integration` image | Running container and local tag used image ID `sha256:087835793fd2993080014d19ee84f8b16abc998b43663e6418f57e465b359c0d` after the event-connection build |

The WeKnora source baseline is the fixed commit
`c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` from
`liugang926/WeKnora_ldapsa`. `scripts/build-weknora.sh` archives that commit
into a temporary build context, checks and applies
`integration/weknora.patch`, builds the Go binary in the local Go image, then
copies the binary and matching migrations onto `weknora-ldap-app:final`.
The adjacent checkout's uncommitted files are not build inputs. The patch is
under version control in this repository; its content and the resulting local
image ID may change during development, so a release should identify the
exact repository commit and rebuild the image from it. The WeKnora `go.mod`
declares Go `1.26.0`; CI uses that file to select its Go toolchain.

The Docker client and server both reported `29.8.0`; Docker Compose reported
`v5.5.1`. These are observations of this workstation, not requirements for
another environment.

## Production decisions still pending

- Validate the intended Nextcloud, PHP, PostgreSQL, Redis, LDAP and team-folder
  compatibility and support windows for the actual deployment target.
- Pin the Go and Node build environments and the WeKnora runtime base to
  reproducible release artifacts with verified registry digests; the local
  `weknora-*` image IDs above do not serve as portable registry references.
- Build and test the final application package and WeKnora artifact from
  reviewed repository commits. Complete the remaining permission, recovery,
  load and fault acceptance work before treating the local matrix as a
  production candidate.

To refresh the observations without displaying configuration secrets, run
`docker compose ps`, `php occ status`, `php -v`, `postgres --version`,
`redis-server --version`, `go version`, `node --version`, `npm --version`, and
`docker image inspect` against the corresponding services and images.
