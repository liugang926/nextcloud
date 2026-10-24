<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Settings;

use OCP\AppFramework\Http\TemplateResponse;
use OCP\Settings\ISettings;

/** Rendered only in Nextcloud's administrator settings. */
final class Admin implements ISettings {
    public function getForm(): TemplateResponse {
        // Ordinary settings must not even prepare legacy operation URLs.
        return new TemplateResponse('integration_weknora', 'admin', [], '');
    }

    public function getSection(): string {
        return 'integration_weknora';
    }

    public function getPriority(): int {
        return 10;
    }
}
