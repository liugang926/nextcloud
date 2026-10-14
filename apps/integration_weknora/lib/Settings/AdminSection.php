<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Settings;

use OCP\IL10N;
use OCP\IURLGenerator;
use OCP\Settings\IIconSection;

final class AdminSection implements IIconSection {
    public function __construct(
        private IL10N $l10n,
        private IURLGenerator $urlGenerator,
    ) {
    }

    public function getIcon(): string {
        return $this->urlGenerator->imagePath('core', 'actions/settings-dark.svg');
    }

    public function getID(): string {
        return 'integration_weknora';
    }

    public function getName(): string {
        return $this->l10n->t('WeKnora publication');
    }

    public function getPriority(): int {
        return 75;
    }
}
