"""Allow ``python -m virtual_you`` to run the ingestion CLI."""

from virtual_you.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
