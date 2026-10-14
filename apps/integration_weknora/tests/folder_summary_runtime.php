<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
\OC_App::loadApp('user_ldap');
set_exception_handler(static function (Throwable $e): void { fwrite(STDERR, (string)$e . "\n"); exit(1); });
use OCA\IntegrationWeknora\AppInfo\Application;
use OCA\IntegrationWeknora\Service\BindingRegistryService;
use OCA\IntegrationWeknora\Service\FilePublicationStateService;
use OCA\IntegrationWeknora\Service\FolderSummaryService;
use OCA\IntegrationWeknora\Service\StaleFolderSummaryException;

function summaryCheck(bool $ok, string $why): void { if (!$ok) { throw new RuntimeException($why); } }
function stalePage(callable $fn): void {
    try { $fn(); } catch (StaleFolderSummaryException $e) { return; }
    throw new RuntimeException('Changed current authority/version accepted an old summary cursor');
}
$services = (new Application())->getContainer(); $uid = 'auto-prd-admin';
$home = \OCP\Server::get(\OCP\Files\IRootFolder::class)->getUserFolder($uid);
$root = $home->newFolder('summary-test-' . bin2hex(random_bytes(4)));
$firstFile = null;
for ($index = 0; $index < 205; $index++) {
    $file = $root->newFile(sprintf('file-%03d.txt', $index), 'Current readable original ' . $index);
    $firstFile ??= $file;
}
$binding = 'summary_' . bin2hex(random_bytes(8));
$registry = $services->get(BindingRegistryService::class);
$registry->save($binding, 'Summary pagination', $uid, $root->getId());
$summary = $services->get(FolderSummaryService::class);
$first = $summary->forUser($uid, $root->getId())['summary'];
summaryCheck($first['total_visible'] === 200 && $first['partial'] && is_string($first['next_cursor']), 'First bounded summary page incorrect');
$second = $summary->forUser($uid, $root->getId(), $first['next_cursor'])['summary'];
summaryCheck($second['total_visible'] === 5 && !$second['partial'] && $second['next_cursor'] === null &&
    $second['generation'] === $first['generation'] && $second['page_offset'] === 200, 'Stable pages did not cover every readable file exactly once');
$duplicate = $summary->forUser($uid, $root->getId(), $first['next_cursor'])['summary'];
summaryCheck($duplicate['total_visible'] === $second['total_visible'], 'Idempotent page retry changed totals');
try { $summary->forUser($uid, $root->getId(), substr($first['next_cursor'], 0, -1) . 'x'); throw new RuntimeException('Forged cursor accepted'); }
catch (InvalidArgumentException $expected) {}
$root->newFile('new-current-file.txt', 'A tree change creates a new generation.');
stalePage(fn () => $summary->forUser($uid, $root->getId(), $first['next_cursor']));
$fresh = $summary->forUser($uid, $root->getId())['summary'];
$services->get(FilePublicationStateService::class)->withdraw($binding, $firstFile->getId(), $uid);
stalePage(fn () => $summary->forUser($uid, $root->getId(), $fresh['next_cursor']));
$afterWithdrawal = $summary->forUser($uid, $root->getId())['summary'];
$registry->setPublicationState($binding, 'stopped', $uid);
stalePage(fn () => $summary->forUser($uid, $root->getId(), $afterWithdrawal['next_cursor']));
summaryCheck($summary->forUser($uid, $root->getId())['summary']['total_visible'] === 0,
    'Stopped publication retained current source processing totals');
echo "Stable signed folder pagination, exact visible counts, retries, forged cursor rejection and source/tree/publication invalidation passed\n";
