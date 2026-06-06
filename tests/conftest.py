"""Shared test setup.

Point the checkpointer at a throwaway DB *before* anything imports the `nodes`
package (whose __init__ opens the SqliteSaver at import time), so tests never
touch the real state store and import cleanly in CI.
"""

import os
import tempfile

os.environ.setdefault("CODER_STATE_DB", os.path.join(tempfile.gettempdir(), "coder_test_state.db"))
