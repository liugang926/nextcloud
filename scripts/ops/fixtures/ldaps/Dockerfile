FROM debian:12.12-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends samba-ad-dc samba-ad-provision samba-common-bin ldap-utils openssl python3-ldb && rm -rf /var/lib/apt/lists/*
CMD ["samba", "--foreground", "--no-process-group"]
