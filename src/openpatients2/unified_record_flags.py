"""Media availability flags shared by the runtime pipeline and the reference unified record."""
from __future__ import annotations

# Image-manifest statuses meaning the source declares no fetchable image asset (cannot be obtained).
UNOBTAINABLE_FIGURE_STATUSES = frozenset({'missing_image'})


def media_flags(row):
    """{'media_available', 'content_reviewed', 'unavailable_reason'} for one runtime figure manifest row.

    media_available is False only when the source declares no asset; transient fetch failures, rights holds
    or caps are None (unknown), never 'unavailable'. Runtime output is never a reviewed reference.
    """
    status = row.get('status')
    available = True if status == 'ready' else False if status in UNOBTAINABLE_FIGURE_STATUSES else None
    return {'media_available': available, 'content_reviewed': False,
            'unavailable_reason': 'no_declared_asset' if available is False else None}
