<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Migration;
use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;
final class Version0025Date20261010000000 extends SimpleMigrationStep {
 public function changeSchema(IOutput $output,Closure $schemaClosure,array $options): ?ISchemaWrapper {
  $schema=$schemaClosure();
  if (!$schema->hasTable('weknora_root_move')) {
   $t=$schema->createTable('weknora_root_move');
   foreach(['operation_id'=>36,'pair_operation_id'=>36,'binding_id'=>128,'instance_id'=>64,'tenant_id'=>20,'knowledge_base_id'=>128,'data_source_id'=>128,'rule_id'=>128,'owner_uid'=>64,'actor_uid'=>64,'physical_root_key'=>64,'storage_identity'=>64,'old_root_hash'=>64,'new_root_hash'=>64,'journal_stream_id'=>36,'journal_chain_sha256'=>64,'state'=>24] as $name=>$len) {$t->addColumn($name,Types::STRING,['length'=>$len,'notnull'=>true]);}
   foreach(['root_file_id','old_epoch','prepared_epoch','resume_epoch','was_active','journal_sequence','created_at','updated_at'] as $name){$t->addColumn($name,Types::BIGINT,['notnull'=>true,'default'=>0]);}
   foreach(['old_root_path','new_root_path'] as $name){$t->addColumn($name,Types::TEXT,['notnull'=>true]);}
   $t->setPrimaryKey(['operation_id']);$t->addIndex(['binding_id','state'],'weknora_root_move_due');
  }
  if (!$schema->hasTable('weknora_target_move')) {
   $t=$schema->createTable('weknora_target_move');
   foreach(['operation_id'=>36,'old_operation_id'=>36,'new_operation_id'=>36,'old_binding_id'=>128,'new_binding_id'=>128,'root_key'=>64,'rule_id'=>128,'actor_uid'=>64,'state'=>24] as $name=>$len){$t->addColumn($name,Types::STRING,['length'=>$len,'notnull'=>true]);}
   $t->addColumn('request_json',Types::TEXT,['notnull'=>true]);
   foreach(['root_file_id','old_epoch','revision','created_at','updated_at'] as $name){$t->addColumn($name,Types::BIGINT,['notnull'=>true,'default'=>0]);}
   $t->setPrimaryKey(['operation_id']);$t->addIndex(['old_binding_id','state'],'weknora_target_move_due');
  }
  return $schema;
 }
}
