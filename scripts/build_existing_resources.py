"""
Backward-compatible entry point for the full cooling-map workflow.

New work should usually run these steps separately:

    python scripts/fetch_features.py
    python scripts/build_page.py
"""

from __future__ import annotations

try:
    from .build_page_data import main as build_page_main
    from .fetch_features import main as fetch_features_main
except ImportError:  # pragma: no cover - used when run as a plain script
    from scripts.build_page_data import main as build_page_main
    from fetch_features import main as fetch_features_main


def main() -> None:
    fetch_features_main()
    build_page_main([])


if __name__ == "__main__":
    main()
