#!/bin/bash
set -euo pipefail
: "${WEKNORA_PRD_TEST_RUNTIME:?Disposable test runtime opt-in required}"
[[ "$WEKNORA_PRD_TEST_RUNTIME" == "1" ]] || exit 2
install -d -m 700 /var/lib/samba/private/tls
openssl req -x509 -newkey rsa:2048 -nodes -keyout /var/lib/samba/private/tls/ca.key -out /var/lib/samba/private/tls/ca.pem -days 2 -subj /CN=Disposable-PRD-CA 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout /var/lib/samba/private/tls/key.pem -out /tmp/prd.csr -subj /CN=prd-ad 2>/dev/null
printf 'subjectAltName=DNS:prd-ad,DNS:prd-ad.prd.example.test\nextendedKeyUsage=serverAuth\n' >/tmp/prd.ext
openssl x509 -req -in /tmp/prd.csr -CA /var/lib/samba/private/tls/ca.pem -CAkey /var/lib/samba/private/tls/ca.key -CAcreateserial -out /var/lib/samba/private/tls/cert.pem -days 2 -extfile /tmp/prd.ext 2>/dev/null
chmod 600 /var/lib/samba/private/tls/*.key /var/lib/samba/private/tls/key.pem
sed -i '/\[global\]/a\	tls enabled = yes\n\ttls cafile = /var/lib/samba/private/tls/ca.pem\n\ttls certfile = /var/lib/samba/private/tls/cert.pem\n\ttls keyfile = /var/lib/samba/private/tls/key.pem\n\tserver services = -smb -s3fs -nbt -winbindd' /etc/samba/smb.conf
exec samba --foreground --no-process-group
