<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Controller;
use OCA\IntegrationWeknora\Service\DirectoryPublicationService;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\JSONResponse;
use OCP\IRequest;

/** Administrator-only rule registration; default framework auth and CSRF apply. */
final class DirectoryRuleAdminController extends Controller {
    public function __construct(IRequest $request, private DirectoryPublicationService $directories) {
        parent::__construct('integration_weknora', $request);
    }
    public function index(): JSONResponse {
        try { return new JSONResponse(['rules' => $this->directories->rules()], 200, ['Cache-Control' => 'no-store']); }
        catch (\Throwable $e) { return new JSONResponse(['error' => 'rules_unavailable'], 503); }
    }
    public function save(): JSONResponse {
        $rule = $this->request->getParam('rule'); $revision = $this->request->getParam('expected_revision');
        if (!is_array($rule) || !is_int($revision)) { return new JSONResponse(['error' => 'invalid_rule'], 400); }
        try { return new JSONResponse(['rule' => $this->directories->saveRule($rule, $revision)], 200, ['Cache-Control' => 'no-store']); }
        catch (\InvalidArgumentException $e) { return new JSONResponse(['error' => 'invalid_rule'], 400); }
        catch (\DomainException $e) { return new JSONResponse(['error' => 'rule_conflict'], 409); }
        catch (\Throwable $e) { return new JSONResponse(['error' => 'rules_unavailable'], 503); }
    }
}
