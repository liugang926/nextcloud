<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Settings;

use OCP\AppFramework\Http\TemplateResponse;
use OCP\IURLGenerator;
use OCP\Settings\ISettings;

/** Rendered only in Nextcloud's administrator settings. */
final class Admin implements ISettings {
    public function __construct(private IURLGenerator $urlGenerator) {
    }

    public function getForm(): TemplateResponse {
        return new TemplateResponse('integration_weknora', 'admin', [
            'bindingsUrl' => $this->urlGenerator->linkToRoute('integration_weknora.binding_admin.index'),
        ], '');
    }

    public function getSection(): string {
        return 'integration_weknora';
    }

    public function getPriority(): int {
        return 10;
    }
}
