"""Resource ceilings for canonicalized document revisions."""

from __future__ import annotations

from delta_chat.canonical.models import DocumentRevision
from delta_chat.errors import ResourceLimitError


def enforce_revision_limits(doc: DocumentRevision, config: dict) -> None:
    """Reject canonical documents before quadratic or prompt-building work."""
    max_pages = int(config.get("max_pages", 20))
    max_per_page = int(config.get("max_elements_per_page", 3_000))
    max_total = int(config.get("max_total_elements", 20_000))
    max_element_chars = int(config.get("max_chars_per_element", 20_000))
    max_text_chars = int(config.get("max_total_text_chars", 2_000_000))

    if len(doc.pages) > max_pages:
        raise ResourceLimitError(
            "Canonical document exceeds the configured page limit",
            details={"pages": len(doc.pages), "max_pages": max_pages},
        )

    total_elements = 0
    total_text_chars = 0
    for page in doc.pages:
        page_elements = len(page.elements)
        if page_elements > max_per_page:
            raise ResourceLimitError(
                "Canonical page exceeds the configured element limit",
                details={
                    "page": page.page_number,
                    "elements": page_elements,
                    "max_elements_per_page": max_per_page,
                },
            )
        total_elements += page_elements
        total_text_chars += len(page.page_text or "")
        for element in page.elements:
            element_chars = len(element.raw_text or "")
            if element_chars > max_element_chars:
                raise ResourceLimitError(
                    "Canonical element exceeds the configured text limit",
                    details={
                        "page": page.page_number,
                        "element_chars": element_chars,
                        "max_chars_per_element": max_element_chars,
                    },
                )
            total_text_chars += element_chars

    if total_elements > max_total:
        raise ResourceLimitError(
            "Canonical document exceeds the configured element limit",
            details={"elements": total_elements, "max_total_elements": max_total},
        )
    if total_text_chars > max_text_chars:
        raise ResourceLimitError(
            "Canonical document exceeds the configured text limit",
            details={"text_chars": total_text_chars, "max_total_text_chars": max_text_chars},
        )
