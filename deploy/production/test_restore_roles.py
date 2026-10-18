#!/usr/bin/env python3
"""Exercise ownerless checkpoint restores against an isolated PostgreSQL 16.

Run separately from the offline release tests:
    python3 deploy/production/test_restore_roles.py

Requires Docker and uses an existing postgres:16-alpine image (--pull=never).
An already verified PostgreSQL image ID can be passed with --image. Use
--pull=missing only where downloading the official test image is allowed.
The disposable
container has no network, published ports, or host mounts. Only its Unix socket
is used; the trust authentication setting never applies to another service.
"""
import argparse
import shutil
import subprocess
import sys
import time
import unittest
import uuid


class RestoreRoleIntegrationTests(unittest.TestCase):
    ADMIN = 'nextcloud'
    APP = 'oc_admin'
    IMAGE = 'postgres:16-alpine'
    PULL = 'never'

    @classmethod
    def docker(cls, *arguments, data=None, check=True, timeout=30):
        result = subprocess.run(
            ['docker', *arguments], input=data, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
        if check and result.returncode:
            # The fixture contains no credentials. Avoid emitting arbitrary
            # Docker/container environment output even when a command fails.
            raise AssertionError('Isolated PostgreSQL Docker command failed: '
                                 + ' '.join(arguments[:3]))
        return result

    @classmethod
    def execute(cls, *arguments, data=None, check=True):
        return cls.docker('exec', '-i', cls.container, *arguments,
                          data=data, check=check)

    @classmethod
    def sql(cls, database, statement, *, check=True):
        return cls.execute(
            'psql', '--no-psqlrc', '-v', 'ON_ERROR_STOP=1', '-A', '-t',
            '-U', cls.ADMIN, '-d', database, data=statement.encode(), check=check,
        )

    @classmethod
    def remove_container(cls):
        cls.docker('rm', '--force', '--volumes', cls.container, check=False)

    @classmethod
    def setUpClass(cls):
        if not shutil.which('docker'):
            raise RuntimeError('Docker is required for the PostgreSQL restore integration test')
        cls.container = 'nextcloud-restore-role-test-' + uuid.uuid4().hex
        cls.addClassCleanup(cls.remove_container)
        cls.docker(
            'run', '--detach', '--pull=' + cls.PULL, '--name', cls.container,
            '--network', 'none', '--cpus', '0.5', '--memory', '256m',
            '--memory-swap', '256m', '--pids-limit', '128',
            '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev,size=128m',
            '--env', 'POSTGRES_USER=' + cls.ADMIN,
            '--env', 'POSTGRES_DB=postgres',
            '--env', 'POSTGRES_HOST_AUTH_METHOD=trust',
            cls.IMAGE, 'postgres', '-c', 'listen_addresses=', timeout=180,
        )
        for _ in range(120):
            # Wait for the entrypoint to exec the final server, rather than
            # racing its temporary initialization server and shutdown.
            ready = cls.execute(
                'sh', '-c', '[ "$(cat /proc/1/comm)" = postgres ] && '
                'pg_isready -U nextcloud -d postgres', check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise AssertionError('Isolated PostgreSQL did not become ready')

        cls.sql('postgres', """
            CREATE ROLE oc_admin LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                NOREPLICATION NOBYPASSRLS;
            CREATE DATABASE nextcloud_seed OWNER oc_admin TEMPLATE template0;
            CREATE DATABASE other_service OWNER nextcloud TEMPLATE template0;
        """)
        cls.sql('nextcloud_seed', """
            SET ROLE oc_admin;
            CREATE TABLE public.oc_appconfig (
                appid text NOT NULL, configkey text NOT NULL,
                configvalue text NOT NULL, PRIMARY KEY (appid, configkey)
            );
            INSERT INTO public.oc_appconfig VALUES ('core', 'installed_version', 'fixture');
            CREATE TABLE public.oc_authtoken (id bigserial PRIMARY KEY, token text NOT NULL);
            INSERT INTO public.oc_authtoken (token) VALUES ('fixture');
        """)
        cls.sql('other_service', """
            CREATE TABLE public.keep (value text);
            INSERT INTO public.keep VALUES ('unchanged');
        """)
        cls.archive = cls.execute(
            'pg_dump', '-U', cls.ADMIN, '-d', 'nextcloud_seed',
            '--format=custom', '--no-owner', '--no-privileges',
        ).stdout
        if not cls.archive.startswith(b'PGDMP'):
            raise AssertionError('PostgreSQL did not produce a custom-format checkpoint')

    def restore(self, database, owner, *, role=None):
        self.execute('createdb', '-U', self.ADMIN, '--template', 'template0',
                     '--owner', owner, database)
        arguments = ['pg_restore', '--exit-on-error', '--no-owner',
                     '--no-privileges', '-U', self.ADMIN, '-d', database]
        if role is not None:
            arguments.extend(['--role', role])
        self.execute(*arguments, data=self.archive)

    def test_previous_restore_reproduces_application_permission_denied(self):
        self.restore('legacy_restore', self.ADMIN)
        ownership = self.sql('legacy_restore', """
            SELECT DISTINCT pg_get_userbyid(c.relowner)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'S');
        """).stdout.decode().strip()
        self.assertEqual(ownership, self.ADMIN)
        denied = self.sql('legacy_restore', """
            BEGIN;
            SET LOCAL ROLE oc_admin;
            SELECT configvalue FROM public.oc_appconfig;
            ROLLBACK;
        """, check=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn(b'permission denied for table oc_appconfig', denied.stderr)

    def test_restore_as_configured_role_retains_ownership_and_access(self):
        self.restore('nextcloud', self.APP, role=self.APP)
        result = self.sql('nextcloud', """
            SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database();
            SELECT count(*) = 3 AND bool_and(pg_get_userbyid(c.relowner) = 'oc_admin')
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'S');
            SELECT has_schema_privilege('oc_admin', 'public', 'USAGE')
               AND has_schema_privilege('oc_admin', 'public', 'CREATE');
            SELECT has_sequence_privilege('oc_admin', 'public.oc_authtoken_id_seq', 'USAGE')
               AND has_sequence_privilege('oc_admin', 'public.oc_authtoken_id_seq', 'SELECT')
               AND has_sequence_privilege('oc_admin', 'public.oc_authtoken_id_seq', 'UPDATE');
            SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb
               AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
            FROM pg_roles WHERE rolname = 'oc_admin';
        """).stdout.decode().splitlines()
        self.assertEqual(result, [self.APP, 't', 't', 't', 't'])

        # WHERE false checks DML authorization without advancing a sequence or
        # changing a row. Sequence increments would survive transaction rollback.
        access = self.sql('nextcloud', """
            BEGIN;
            SET LOCAL ROLE oc_admin;
            SELECT session_user = 'nextcloud' AND current_user = 'oc_admin';
            SELECT configvalue FROM public.oc_appconfig
                WHERE appid = 'core' AND configkey = 'installed_version';
            UPDATE public.oc_appconfig SET configvalue = configvalue WHERE false;
            INSERT INTO public.oc_appconfig SELECT 'probe', 'probe', 'probe' WHERE false;
            DELETE FROM public.oc_appconfig WHERE false;
            CREATE TABLE public.restore_role_probe (id bigint);
            ROLLBACK;
        """).stdout.decode().splitlines()
        self.assertIn('t', access)
        self.assertIn('fixture', access)
        self.assertEqual(self.sql('nextcloud', """
            SELECT count(*) FROM public.oc_appconfig;
            SELECT last_value FROM public.oc_authtoken_id_seq;
            SELECT to_regclass('public.restore_role_probe') IS NULL;
        """).stdout.decode().splitlines(), ['1', '1', 't'])
        self.assertEqual(self.sql('other_service', 'SELECT value FROM public.keep;')
                         .stdout.decode().strip(), 'unchanged')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=RestoreRoleIntegrationTests.IMAGE,
                        help='Existing PostgreSQL 16 image tag or verified content ID')
    parser.add_argument('--pull', choices=('never', 'missing'), default='never',
                        help='Image pull policy (default: never)')
    options, test_arguments = parser.parse_known_args()
    RestoreRoleIntegrationTests.IMAGE = options.image
    RestoreRoleIntegrationTests.PULL = options.pull
    unittest.main(argv=[sys.argv[0], *test_arguments], verbosity=2)
