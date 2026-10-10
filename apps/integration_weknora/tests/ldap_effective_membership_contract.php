<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/Service/LiveLdapGuidVerifier.php';
use OCA\IntegrationWeknora\Service\LiveLdapGuidVerifier;

// Real binary SID equality includes domain and RID; names and a matching RID
// in another directory/domain cannot confer membership.
$primary = hex2bin('01050000000000051500000001000000020000000300000001020000');
$nested = hex2bin('01050000000000051500000001000000020000000300000003020000');
$other = hex2bin('01050000000000051500000011000000020000000300000001020000');
$tokens = ['count' => 2, 0 => $primary, 1 => $nested];
if (!LiveLdapGuidVerifier::containsSid($primary, $tokens) ||
    !LiveLdapGuidVerifier::containsSid($nested, $tokens) ||
    LiveLdapGuidVerifier::containsSid($other, $tokens)) {
    throw new RuntimeException('Effective security-group SID comparison failed');
}
foreach ([['count' => 2, 0 => $primary], ['count' => 1, 0 => 'named-group'], ['count' => -1]] as $invalid) {
    try { LiveLdapGuidVerifier::containsSid($primary, $invalid); }
    catch (RuntimeException $e) { continue; }
    throw new RuntimeException('Incomplete directory membership was treated as a complete snapshot');
}
echo "binary AD effective-group membership contract passed (live LDAPS requires separate acceptance)\n";
