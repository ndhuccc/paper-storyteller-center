#!/usr/bin/env python3
"""Entry point for Paper Story Rewriting Center Flask server."""
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webapp import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8501))
    app.run(host="0.0.0.0", port=port, debug=False)
