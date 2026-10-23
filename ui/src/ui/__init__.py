import sys
from pathlib import Path


def main() -> None:
    """Entry point for `uv run ui`: starts Streamlit on app.py (same as `streamlit run app.py`)."""
    from streamlit.web import cli

    sys.argv = ["streamlit", "run", str(Path(__file__).parent / "app.py"), *sys.argv[1:]]
    sys.exit(cli.main())
