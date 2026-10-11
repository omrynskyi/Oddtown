import os

# Importing observer.app builds the module-level app. Keep it off the real observer.db.
os.environ["OBSERVER_DB"] = ":memory:"
