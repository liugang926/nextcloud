<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IConfig;
use Psr\Log\LoggerInterface;

/** Emits an alert on a new health state and a recovery when it clears. */
final class EventHealthMonitor {
    private const APP_ID = 'integration_weknora';
    private const FINGERPRINT_KEY = 'event_health_alert_fingerprint';
    private const REMINDER_KEY = 'event_health_last_alert_at';
    private const REMINDER_SECONDS = 3600;

    public function __construct(
        private OperationalStatusService $status,
        private IConfig $config,
        private LoggerInterface $logger,
    ) {
    }

    public function check(?int $now = null): void {
        $now ??= time();
        try {
            $alerts = EventHealthEvaluator::evaluate($this->status->snapshot());
        } catch (\Throwable $exception) {
            // Error messages from dependencies can contain URLs or paths.
            $alerts = [['binding_id' => '', 'code' => 'diagnostics_unavailable']];
        }
        $fingerprint = $alerts === [] ? '' : hash('sha256', json_encode($alerts, JSON_THROW_ON_ERROR));
        $previous = $this->config->getAppValue(self::APP_ID, self::FINGERPRINT_KEY, '');
        $lastAlertAt = (int)$this->config->getAppValue(self::APP_ID, self::REMINDER_KEY, '0');

        if ($alerts === [] && $previous !== '') {
            $this->logger->info('WeKnora integration event health recovered', [
                'app' => self::APP_ID,
            ]);
        } elseif ($alerts !== [] &&
            ($fingerprint !== $previous || $now - $lastAlertAt >= self::REMINDER_SECONDS)) {
            // Only stable codes and binding IDs leave the monitor. Do not log
            // credentials, remote addresses, file paths, or raw exceptions.
            $this->logger->warning('WeKnora integration event health alert', [
                'app' => self::APP_ID,
                'alerts' => $alerts,
            ]);
            $this->config->setAppValue(self::APP_ID, self::REMINDER_KEY, (string)$now);
        }
        if ($fingerprint !== $previous) {
            $this->config->setAppValue(self::APP_ID, self::FINGERPRINT_KEY, $fingerprint);
        }
    }
}
