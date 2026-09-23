<?php

declare(strict_types=1);

return [
    'routes' => [
        ['name' => 'api#capabilities', 'url' => '/api/v1/capabilities', 'verb' => 'GET'],
        ['name' => 'api#bindings', 'url' => '/api/v1/bindings', 'verb' => 'GET'],
        [
            'name' => 'api#manifest',
            'url' => '/api/v1/bindings/{id}/manifest',
            'verb' => 'GET',
            'requirements' => ['id' => '[A-Za-z0-9_-]+'],
        ],
        [
            'name' => 'api#content',
            'url' => '/api/v1/bindings/{id}/files/{fileId}/content',
            'verb' => 'GET',
            'requirements' => ['id' => '[A-Za-z0-9_-]+', 'fileId' => '[1-9][0-9]*'],
        ],
        [
            'name' => 'changes#index',
            'url' => '/api/v1/bindings/{id}/changes',
            'verb' => 'GET',
            'requirements' => ['id' => '[A-Za-z0-9_-]+'],
        ],
        [
            'name' => 'authorization#authorize',
            'url' => '/api/v1/bindings/{id}/authorize',
            'verb' => 'POST',
            'requirements' => ['id' => '[A-Za-z0-9_-]+'],
        ],
        ['name' => 'identity_admin#index', 'url' => '/api/v1/admin/identities', 'verb' => 'GET'],
        ['name' => 'diagnostics#index', 'url' => '/api/v1/admin/diagnostics', 'verb' => 'GET'],
        ['name' => 'identity_admin#create', 'url' => '/api/v1/admin/identities', 'verb' => 'POST'],
        ['name' => 'identity_admin#revoke', 'url' => '/api/v1/admin/identities/revoke', 'verb' => 'POST'],
        [
            'name' => 'employee_file_status#show',
            'url' => '/api/v1/files/{fileId}/status',
            'verb' => 'GET',
            'requirements' => ['fileId' => '[1-9][0-9]*'],
        ],
        [
            'name' => 'publication#state',
            'url' => '/api/v1/admin/bindings/{id}/files/{fileId}/publication',
            'verb' => 'GET',
            'requirements' => ['id' => '[A-Za-z0-9_-]+', 'fileId' => '[1-9][0-9]*'],
        ],
        ['name' => 'binding_admin#index', 'url' => '/api/v1/admin/bindings', 'verb' => 'GET'],
        ['name' => 'binding_admin#save', 'url' => '/api/v1/admin/bindings', 'verb' => 'POST'],
        [
            'name' => 'publication#withdraw',
            'url' => '/api/v1/admin/bindings/{id}/files/{fileId}/withdraw',
            'verb' => 'POST',
            'requirements' => ['id' => '[A-Za-z0-9_-]+', 'fileId' => '[1-9][0-9]*'],
        ],
        [
            'name' => 'publication#republish',
            'url' => '/api/v1/admin/bindings/{id}/files/{fileId}/republish',
            'verb' => 'POST',
            'requirements' => ['id' => '[A-Za-z0-9_-]+', 'fileId' => '[1-9][0-9]*'],
        ],
    ],
];
