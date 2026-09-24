#!/usr/bin/env python3
"""Verify an isolated AD-shaped LDAP snapshot before permission-matrix phases.

Input is an attribute-limited LDIF captured immediately before the HTTP
matrix. This checks the *directory topology*; it does not substitute for the
Nextcloud and WeKnora authorization probes or real Microsoft AD acceptance.
"""

import base64
import binascii
from collections import defaultdict
from pathlib import Path
import re


GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
MAX_LDIF_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 1000


class TopologyError(ValueError):
    pass


def canonical_guid(raw):
    if len(raw) != 16:
        raise TopologyError("objectGUID must contain 16 binary bytes")
    return (raw[0:4][::-1].hex() + "-" + raw[4:6][::-1].hex() + "-" +
            raw[6:8][::-1].hex() + "-" + raw[8:10].hex() + "-" + raw[10:16].hex())


def canonical_sid(raw):
    if len(raw) < 8 or raw[0] != 1 or len(raw) != 8 + 4 * raw[1]:
        raise TopologyError("objectSid has invalid binary structure")
    authority = int.from_bytes(raw[2:8], "big")
    subauthorities = [str(int.from_bytes(raw[8 + i * 4:12 + i * 4], "little"))
                      for i in range(raw[1])]
    return "S-1-" + str(authority) + "".join("-" + part for part in subauthorities)


def norm_dn(value):
    # This deliberately covers simple synthetic DNs only. Escaped commas or
    # multi-valued RDNs should be handled by a real LDAP DN parser in AD runs.
    if "\\" in value or "+" in value or not value.strip():
        raise TopologyError("snapshot contains a complex or empty DN")
    return ",".join(part.strip().casefold() for part in value.split(","))


def read_ldif(path):
    if path.stat().st_size > MAX_LDIF_BYTES:
        raise TopologyError("LDAP snapshot exceeds the synthetic size limit")
    lines = path.read_text(encoding="utf-8").splitlines()
    unfolded = []
    for line in lines:
        if line.startswith(" "):
            if not unfolded:
                raise TopologyError("orphan LDIF continuation line")
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    entries = []
    current = defaultdict(list)
    for line in unfolded + [""]:
        if not line:
            if current:
                entries.append(dict(current))
                current = defaultdict(list)
            continue
        if line.startswith("#") or line.startswith("version:"):
            continue
        if ":<" in line:
            raise TopologyError("LDIF URL values are not accepted")
        key, separator, value = line.partition(":")
        if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", key):
            raise TopologyError("invalid LDIF attribute line")
        if value.startswith(":"):
            try:
                decoded = base64.b64decode(value[1:].strip(), validate=True)
            except binascii.Error as error:
                raise TopologyError("invalid LDIF base64 value") from error
        elif value.startswith(" "):
            decoded = value[1:].encode("utf-8")
        else:
            raise TopologyError("invalid LDIF value separator")
        current[key.casefold()].append(decoded)
    if len(entries) > MAX_ENTRIES:
        raise TopologyError("LDAP snapshot exceeds the synthetic entry limit")
    return entries


def one(entry, key):
    values = entry.get(key, [])
    if len(values) != 1:
        raise TopologyError(f"LDAP entry needs exactly one {key}")
    return values[0]


def text_value(raw, field):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TopologyError(f"invalid UTF-8 {field}") from error


def topology(path):
    by_guid, by_dn, groups = {}, {}, set()
    for entry in read_ldif(path):
        if "objectguid" not in entry:
            continue
        dn = norm_dn(text_value(one(entry, "dn"), "dn"))
        guid = canonical_guid(one(entry, "objectguid"))
        if dn in by_dn or guid in by_guid:
            raise TopologyError("duplicate DN or objectGUID in LDAP snapshot")
        classes = {text_value(raw, "objectClass").casefold()
                   for raw in entry.get("objectclass", [])}
        is_group = bool(classes & {"group", "groupofnames", "groupofuniquenames", "adtestgroup"})
        sid = canonical_sid(one(entry, "objectsid"))
        primary = None
        if "primarygroupid" in entry:
            value = text_value(one(entry, "primarygroupid"), "primaryGroupID")
            if not value.isdecimal():
                raise TopologyError("primaryGroupID is not an integer")
            primary = sid.rsplit("-", 1)[0] + "-" + value
        members = {norm_dn(text_value(raw, "member")) for raw in
                   entry.get("member", []) + entry.get("uniquemember", [])}
        node = {"guid": guid, "dn": dn, "sid": sid, "primary_sid": primary,
                "is_group": is_group, "members": members}
        by_guid[guid] = by_dn[dn] = node
        if is_group:
            groups.add(guid)
    if not by_guid:
        raise TopologyError("LDAP snapshot has no GUID-bearing entries")
    group_by_sid = {}
    for guid in groups:
        sid = by_guid[guid]["sid"]
        if sid in group_by_sid:
            raise TopologyError("duplicate group objectSid in LDAP snapshot")
        group_by_sid[sid] = guid
    parents = defaultdict(set)
    direct = defaultdict(set)
    for parent_guid in groups:
        parent = by_guid[parent_guid]
        for dn in parent["members"]:
            member = by_dn.get(dn)
            if member is None:
                # A directory may include service principals outside the
                # limited user/group export. Missing *groups* could hide a
                # nested path, so refuse those in the synthetic fixture.
                if ",ou=groups," in dn:
                    raise TopologyError("nested group is missing from LDAP snapshot")
                continue
            if member["is_group"]:
                parents[member["guid"]].add(parent_guid)
            else:
                direct[member["guid"]].add(parent_guid)

    visiting, visited = set(), set()

    def check_cycle(group_guid):
        if group_guid in visiting:
            raise TopologyError("LDAP group nesting contains a cycle")
        if group_guid in visited:
            return
        visiting.add(group_guid)
        for parent in parents[group_guid]:
            check_cycle(parent)
        visiting.remove(group_guid)
        visited.add(group_guid)

    for group_guid in groups:
        check_cycle(group_guid)

    def effective(user_guid):
        user = by_guid[user_guid]
        starting = set(direct[user_guid])
        if user["primary_sid"] in group_by_sid:
            starting.add(group_by_sid[user["primary_sid"]])
        result = set(starting)
        pending = list(starting)
        while pending:
            for parent in parents[pending.pop()]:
                if parent not in result:
                    result.add(parent)
                    pending.append(parent)
        return result

    return by_guid, direct, parents, effective


def validate_topology(data, case, path, grant_guid, child_guid=None):
    if data.get("synthetic_fixture") is not True:
        raise TopologyError("topology requires an explicitly synthetic fixture")
    if case not in {"primary_group", "nested_group"}:
        raise TopologyError("topology phase must be primary_group or nested_group")
    if not GUID.fullmatch(grant_guid) or (child_guid is not None and not GUID.fullmatch(child_guid)):
        raise TopologyError("group GUIDs must be canonical UUIDs")
    grant_guid = grant_guid.lower()
    child_guid = child_guid.lower() if child_guid else None
    by_guid, direct, parents, effective = topology(Path(path))
    a_guid = data["accounts"]["a"]["object_guid"].lower()
    b_guid = data["accounts"]["b"]["object_guid"].lower()
    for guid in (a_guid, b_guid, grant_guid):
        if guid not in by_guid:
            raise TopologyError("fixture user or grant group missing from LDAP snapshot")
    if by_guid[a_guid]["is_group"] or by_guid[b_guid]["is_group"] or not by_guid[grant_guid]["is_group"]:
        raise TopologyError("fixture account/group types are inconsistent")
    if grant_guid in effective(b_guid):
        raise TopologyError("account B is a member of the grant group")
    if grant_guid not in effective(a_guid):
        raise TopologyError("account A is not an effective member of the grant group")
    if case == "primary_group":
        if by_guid[a_guid]["primary_sid"] != by_guid[grant_guid]["sid"]:
            raise TopologyError("A's primaryGroupID does not resolve to the grant group")
        if grant_guid in direct[a_guid] or any(
                grant_guid in effective_group_parents(group, parents) for group in direct[a_guid]):
            raise TopologyError("A has an alternate direct/nested grant path")
    else:
        if child_guid is None or child_guid not in by_guid or not by_guid[child_guid]["is_group"]:
            raise TopologyError("nested_group needs a distinct child group in the snapshot")
        if child_guid == grant_guid or child_guid not in direct[a_guid] or grant_guid not in parents[child_guid]:
            raise TopologyError("A must belong directly to a child nested in the grant group")
        alternate_direct = any(
            group != child_guid and
            (group == grant_guid or grant_guid in effective_group_parents(group, parents))
            for group in direct[a_guid])
        primary_group = next((guid for guid, node in by_guid.items() if node["is_group"] and
                              node["sid"] == by_guid[a_guid]["primary_sid"]), None)
        alternate_primary = primary_group is not None and (
            primary_group == grant_guid or
            grant_guid in effective_group_parents(primary_group, parents))
        if alternate_direct or alternate_primary:
            raise TopologyError("A has a direct or primary grant path, masking nesting")
    return {"case": case, "grant_group_guid": grant_guid,
            "a_effective": True, "b_effective": False,
            "sole_primary_path" if case == "primary_group" else "nested_edge_verified": True}


def effective_group_parents(group_guid, parents):
    result, pending = set(), [group_guid]
    while pending:
        for parent in parents[pending.pop()]:
            if parent not in result:
                result.add(parent)
                pending.append(parent)
    return result
