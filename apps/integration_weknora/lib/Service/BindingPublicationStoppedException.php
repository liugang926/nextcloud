<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** A configured binding is deliberately closed to publication reads. */
final class BindingPublicationStoppedException extends \RuntimeException {
}
