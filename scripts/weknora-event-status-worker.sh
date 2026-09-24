#!/bin/sh
set -eu

cd /var/www/html
while :; do
    # Status reads are bounded and run in a separate process from delivery.
    # The connection row lock still fences rotation, revocation and pruning.
    if timeout -k 5s 60s php occ integration_weknora:poll-event-status --quiet; then
        :
    else
        printf '%s\n' 'WeKnora applied status pass failed; retrying' >&2
    fi
    sleep 5
done
