"""Write the finished session into the Google Drive folder.

Drive for Desktop mounts as a normal filesystem path, so this is a plain write —
no OAuth, no API client. Connect the folder to a claude.ai Project once and
every later session appears there.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date

from . import config, formats

log = logging.getLogger(__name__)


def slug(title: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return (cleaned or fallback)[:60]


def write(session: dict, utterances: list[dict]) -> str | None:
    """Return the written path, or None if delivery was skipped.

    Delivery is deliberately non-fatal: a missing Drive folder or a sync hiccup
    should never lose a transcript that already processed successfully.
    """
    target_dir = config.DRIVE_DIR
    if not target_dir.parent.exists():
        log.warning(
            "Skipping Drive delivery: %s does not exist. Is Google Drive for "
            "Desktop running?", target_dir.parent,
        )
        return None

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        created = (session.get("created_at") or "")[:10] or date.today().isoformat()
        name = f"{created}-{slug(session.get('title', ''), session['id'])}.md"
        path = target_dir / name
        path.write_text(formats.bundle(session, utterances), encoding="utf-8")
        log.info("Delivered %s to %s", session["id"], path)
        return str(path)
    except OSError as exc:
        log.warning("Drive delivery failed for %s: %s", session.get("id"), exc)
        return None
