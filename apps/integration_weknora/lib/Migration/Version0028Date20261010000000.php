<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Migration;
use Closure;
use Doctrine\DBAL\Types\Types;
use OCP\DB\ISchemaWrapper;
use OCP\Migration\IOutput;
use OCP\Migration\SimpleMigrationStep;
final class Version0028Date20261010000000 extends SimpleMigrationStep {
 public function changeSchema(IOutput $output,Closure $schemaClosure,array $options): ?ISchemaWrapper {
  $s=$schemaClosure();
  if(!$s->hasTable('weknora_recovery_floor')){$t=$s->createTable('weknora_recovery_floor');
   foreach(['file_id','revision','writing','verified_at'] as $c){$t->addColumn($c,Types::BIGINT,['notnull'=>true,'default'=>0]);}
   $t->addColumn('plan_sha256',Types::STRING,['notnull'=>true,'length'=>64]);$t->setPrimaryKey(['file_id']);}
  if(!$s->hasTable('weknora_control_gate')){$t=$s->createTable('weknora_control_gate');$t->addColumn('id',Types::INTEGER,['notnull'=>true]);
   foreach(['closed','closed_at'] as $c){$t->addColumn($c,Types::BIGINT,['notnull'=>true,'default'=>0]);}
   foreach(['plan_sha256','inventory_sha256'] as $c){$t->addColumn($c,Types::STRING,['notnull'=>true,'length'=>64]);}$t->setPrimaryKey(['id']);}
  return $s;
 }
}
