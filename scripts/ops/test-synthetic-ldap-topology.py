#!/usr/bin/env python3
"""Offline contracts for fresh synthetic LDAP topology preflight."""

import base64
from pathlib import Path
import tempfile
import unittest
import uuid

from synthetic_ldap_topology import TopologyError, validate_topology


A_GUID = "11111111-2222-3333-4444-555555555555"
B_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
GRANT_GUID = "22222222-3333-4444-5555-666666666666"
CHILD_GUID = "33333333-4444-5555-6666-777777777777"
BASE = "dc=example,dc=test"
A_DN = "cn=Alice,ou=people," + BASE
B_DN = "cn=Bob,ou=people," + BASE
GRANT_DN = "cn=Grant,ou=groups," + BASE
CHILD_DN = "cn=Child,ou=groups," + BASE
DOMAIN_SID = (21, 1000, 2000, 3000)


def binary_sid(rid):
    parts = DOMAIN_SID + (rid,)
    return bytes((1, len(parts))) + (5).to_bytes(6, "big") + b"".join(
        part.to_bytes(4, "little") for part in parts)


def entry(dn, kind, guid, rid, *, primary=None, members=()):
    attributes = ["dn: " + dn, "objectClass: " + kind,
                  "objectGUID:: " + base64.b64encode(uuid.UUID(guid).bytes_le).decode(),
                  "objectSid:: " + base64.b64encode(binary_sid(rid)).decode()]
    if primary is not None:
        attributes.append("primaryGroupID: " + str(primary))
    attributes.extend("member: " + member for member in members)
    return "\n".join(attributes) + "\n\n"


def snapshot(*, mode="nested", b_in_child=False, cycle=False, direct_grant=False):
    primary_a = 2000 if mode == "primary" else 513
    direct = [A_DN] if direct_grant else []
    if mode == "nested":
        direct.append(CHILD_DN)
    rows = [entry(A_DN, "adTestUser", A_GUID, 1100, primary=primary_a),
            entry(B_DN, "adTestUser", B_GUID, 1101, primary=513),
            entry(GRANT_DN, "adTestGroup", GRANT_GUID, 2000, members=direct)]
    if mode == "nested":
        child_members = [A_DN]
        if b_in_child:
            child_members.append(B_DN)
        if cycle:
            child_members.append(GRANT_DN)
        rows.append(entry(CHILD_DN, "adTestGroup", CHILD_GUID, 2001,
                          members=child_members))
    return "".join(rows)


def fixture():
    return {"synthetic_fixture": True, "accounts": {
        "a": {"object_guid": A_GUID}, "b": {"object_guid": B_GUID}}}


class SyntheticTopologyTest(unittest.TestCase):
    def validate(self, content, case, child=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.ldif"
            path.write_text(content, encoding="utf-8")
            return validate_topology(fixture(), case, path, GRANT_GUID, child)

    def test_primary_group_is_the_sole_grant_path(self):
        report = self.validate(snapshot(mode="primary"), "primary_group")
        self.assertTrue(report["sole_primary_path"])
        with self.assertRaisesRegex(TopologyError, "alternate"):
            self.validate(snapshot(mode="primary", direct_grant=True), "primary_group")

    def test_nested_group_requires_child_edge_and_excludes_b(self):
        report = self.validate(snapshot(), "nested_group", CHILD_GUID)
        self.assertTrue(report["nested_edge_verified"])
        with self.assertRaisesRegex(TopologyError, "account B"):
            self.validate(snapshot(b_in_child=True), "nested_group", CHILD_GUID)
        with self.assertRaisesRegex(TopologyError, "direct or primary"):
            self.validate(snapshot(direct_grant=True), "nested_group", CHILD_GUID)

    def test_missing_edge_cycle_and_duplicate_guid_fail(self):
        with self.assertRaisesRegex(TopologyError, "child group"):
            self.validate(snapshot(), "nested_group", "44444444-5555-6666-7777-888888888888")
        with self.assertRaisesRegex(TopologyError, "cycle"):
            self.validate(snapshot(cycle=True), "nested_group", CHILD_GUID)
        duplicate = snapshot() + entry("cn=Duplicate,ou=people," + BASE,
                                       "adTestUser", A_GUID, 1200, primary=513)
        with self.assertRaisesRegex(TopologyError, "duplicate"):
            self.validate(duplicate, "nested_group", CHILD_GUID)

    def test_malformed_binary_and_ldif_url_fail_closed(self):
        content = snapshot().replace("objectGUID:: " + base64.b64encode(uuid.UUID(A_GUID).bytes_le).decode(),
                                     "objectGUID:: AQID", 1)
        with self.assertRaisesRegex(TopologyError, "objectGUID"):
            self.validate(content, "nested_group", CHILD_GUID)
        with self.assertRaisesRegex(TopologyError, "URL"):
            self.validate(snapshot() + "dn:< file:///etc/passwd\n\n",
                          "nested_group", CHILD_GUID)


if __name__ == "__main__":
    unittest.main()
