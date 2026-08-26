"""
Run access for the API — a thin shim over db.runs_store, kept so routes
import run accessors from one place. Runs are persisted (DB + files under
data/runs/) and survive restarts; the old in-memory dict is gone.
"""

from db import runs_store

get_run = runs_store.get_run
get_frame = runs_store.get_frame
frame_names = runs_store.frame_names
list_runs = runs_store.list_runs
persist_success = runs_store.persist_success
persist_failure = runs_store.persist_failure
