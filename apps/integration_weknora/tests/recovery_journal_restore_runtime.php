<?php
declare(strict_types=1);
if (getenv('WEKNORA_PRD_TEST_RUNTIME') !== '1') { throw new RuntimeException('Disposable runtime required'); }
define('OC_CONSOLE', 1);
require '/var/www/html/lib/base.php';
require_once dirname(__DIR__).'/lib/Service/PublicationRecoveryLedger.php';
use OCA\IntegrationWeknora\Service\PublicationRecoveryLedger as Ledger;
set_exception_handler(static function(Throwable $e):void { fwrite(STDERR,(string)$e."\n"); exit(1); });
$db=\OCP\Server::get(\OCP\IDBConnection::class);
$configDir=sys_get_temp_dir().'/journal-config-'.bin2hex(random_bytes(6));mkdir($configDir,0700);
file_put_contents($configDir.'/config.php','<?php $CONFIG='.var_export(['maintenance'=>true,'instanceid'=>'journal-fixture'],true).';');
$config=new \OC\AllConfig(new \OC\SystemConfig(new \OC\Config($configDir.'/')));
$ledger=new Ledger($db,$config);
function checkJournal(bool $value):void { if(!$value){throw new RuntimeException('Journal recovery invariant failed');} }
$db->beginTransaction();try {
 $q=$db->getQueryBuilder();$r=$q->select('stream_id','sequence','chain_sha256')->from('weknora_recovery_head')->where($q->expr()->eq('id',$q->createNamedParameter(1)))->executeQuery();try{$head=$r->fetchAssociative();}finally{$r->closeCursor();}
 $start=(int)$head['sequence'];$event=['sequence'=>(string)($start+1),'binding_id'=>'journal-test','file_id'=>'123','kind'=>'withdrawn','source_revision'=>'2','created_at'=>time()];$chain=Ledger::eventHash($head['chain_sha256'],$event);
 $plan=['instance_id'=>'journal-fixture','stream_id'=>$head['stream_id'],'checkpoint_sequence'=>(string)$start,'through_sequence'=>(string)($start+1),'checkpoint_database_chain_sha256'=>$head['chain_sha256'],'through_database_chain_sha256'=>$chain,'database_prefix_hashes'=>[(string)$start=>$head['chain_sha256'],(string)($start+1)=>$chain],'journal_events'=>[$event]];
 foreach(['altered','missing','fork'] as $case){
  $bad=$plan;if($case==='altered'){$bad['journal_events'][0]['kind']='eligible';}elseif($case==='missing'){$bad['journal_events']=[];}else{$bad['database_prefix_hashes'][(string)$start]=str_repeat('f',64);}
  try{$ledger->restoreTail($bad);throw new RuntimeException('Accepted bad journal '.$case);}catch(DomainException $e){}
  checkJournal($ledger->page($start)['items']===[]);
 }
 $ledger->restoreTail($plan);$ledger->restoreTail($plan);checkJournal($ledger->page($start)['items']===[$event]);
 Ledger::appendInTransaction($db,'journal-test',123,'binding_stop',3);
 $page=$ledger->page($start+1);checkJournal($page['after_chain_sha256']===$chain&&count($page['items'])===1);
 echo "Authenticated journal restore: tampered/missing/fork rejected atomically; exact replay idempotent; closure append preserves retained external prefix. PASS\n";
}finally{$db->rollBack();unlink($configDir.'/config.php');rmdir($configDir);}
