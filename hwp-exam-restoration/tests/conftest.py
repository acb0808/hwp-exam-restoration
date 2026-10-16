"""The user's own saved defaults (~/.hwp-restoration/preferences.json) never reach a test run."""
import os
import tempfile
from pathlib import Path

os.environ['HWP_PREFERENCES'] = str(Path(tempfile.gettempdir()) / 'hwp-tests-no-preferences' / 'preferences.json')
