#!/usr/bin/env python3
"""Keep the selected-Space knowledge-base chooser unambiguous."""

from pathlib import Path


APP = Path(__file__).resolve().parents[1]
SOURCE = (APP / "src" / "sidebar.js").read_text(encoding="utf-8")
BUILT = (APP / "js" / "weknora-sidebar.js").read_text(encoding="utf-8")
CSS = (APP / "css" / "weknora-sidebar.css").read_text(encoding="utf-8")
SERVICE = (APP / "lib" / "Service" / "DirectoryPublicationService.php").read_text(
    encoding="utf-8"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


require(
    "params.set('tenant_id', spaceId)" in SOURCE,
    "selected Space must be sent as an explicit tenant filter",
)
require(
    "option.value = candidate.id" in SOURCE
    and "option.dataset.source = 'weknora'" in SOURCE
    and "option.dataset.knowledgeBaseId = candidate.id" in SOURCE,
    "knowledge-base options must carry the WeKnora candidate identity",
)
require(
    "option.textContent = candidate.name" in SOURCE
    and "option.title = `WeKnora 知识库 ID：${candidate.id}`" in SOURCE,
    "knowledge-base options must show only the name and expose the ID on hover",
)
require(
    "已有 ${contentCount} 条内容" not in SOURCE
    and "candidate.configuration_summary" not in SOURCE,
    "knowledge-base option labels must not mix counts or configuration details into the name",
)
require(
    "这里是 WeKnora 知识库，不是 Nextcloud 目录" in SOURCE,
    "the chooser must explicitly distinguish knowledge bases from directories",
)
require(
    "mode.value = 'existing'" in SOURCE
    and "new Option('创建新知识库', 'create')" not in SOURCE,
    "the directory chooser must only associate existing WeKnora knowledge bases",
)
require(
    ".weknora-sidebar__form [hidden] { display: none !important; }" in CSS,
    "the form stylesheet must not override the hidden attribute",
)
require(
    'option.dataset.source = "weknora"' in BUILT
    and "\\u4E0D\\u662F Nextcloud \\u76EE\\u5F55" in BUILT,
    "the committed sidebar bundle must include the clarified labels",
)

print("Selected-Space knowledge-base chooser labels and identity contract passed")
