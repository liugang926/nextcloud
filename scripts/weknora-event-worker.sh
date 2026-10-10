#!/bin/sh
set -eu

cd /var/www/html
while :; do
    # Each pass is bounded. If PHP is interrupted after a remote receipt but
    # before its local checkpoint, the receiver's idempotent receipt handles
    # the next attempt. Keep ordinary cron for all other Nextcloud jobs.
    if timeout -k 5s 90s php occ integration_weknora:deliver-events --quiet; then
        :
    else
        printf '%s\n' 'WeKnora event delivery pass failed; retrying' >&2
    fi
    sleep 5
done
