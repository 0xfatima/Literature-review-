"""Cross-language contract checks between the backend and the hand-written frontend constants.
(Full OpenAPI -> TypeScript generation is still on the roadmap; these guard the values the UI branches on.)"""

import pathlib
import re

from app.db.models import MESSAGE_ROLES, PROJECT_STATUSES

WEB = pathlib.Path(__file__).resolve().parents[4] / "apps" / "web" / "src" / "lib"


def test_frontend_stage_track_covers_every_backend_project_status():
    src = (WEB / "stages.ts").read_text()
    pipeline = re.findall(r'\{ id: "([a-z_]+)", label: "[^"]+" \}', src.split("export const ATTENTION")[0])
    attention = re.search(r'ATTENTION = \{ id: "([a-z_]+)"', src)
    assert attention, "ATTENTION constant not found in stages.ts"
    assert sorted([*pipeline, attention.group(1)]) == sorted(PROJECT_STATUSES)
    # Order matters for the stage track: same order as the backend, with needs_attention outside it.
    assert pipeline == [s for s in PROJECT_STATUSES if s != "needs_attention"]


def test_frontend_message_roles_match_backend():
    src = (WEB / "types.ts").read_text()
    m = re.search(r"role: ((?:\"[a-z]+\"(?: \| )?)+);", src)
    assert m, "Message.role union not found in types.ts"
    assert sorted(re.findall(r'"([a-z]+)"', m.group(1))) == sorted(MESSAGE_ROLES)


def test_frontend_workflow_stages_match_backend():
    from app.protocol.stages import STAGES

    src = (WEB / "protocol.ts").read_text()
    block = src.split("export const WORKFLOW_STAGES = [")[1].split("]")[0]
    assert re.findall(r'"([A-Z_]+)"', block) == list(STAGES)


def test_frontend_frameworks_match_backend():
    from app.protocol.frameworks import FRAMEWORKS

    src = (WEB / "protocol.ts").read_text().split("export const FRAMEWORKS")[1].split("export function")[0]
    keys = re.findall(r'^(?:\s{2}\{ key|\s{4}key): "([a-z_]+)"', src, re.M)
    assert keys == list(FRAMEWORKS)
    for fw in FRAMEWORKS.values():
        for e in fw.elements:
            assert f'{{ key: "{e.key}", label: "{e.label}" }}' in src, (fw.key, e.key)
