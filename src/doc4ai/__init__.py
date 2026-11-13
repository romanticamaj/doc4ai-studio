import os as _os

# renamed from aidoc: settings under the old prefix keep working, the new name wins when both are set
for _k, _v in list(_os.environ.items()):
    if _k.startswith("AIDOC_"):
        _os.environ.setdefault("DOC4AI_" + _k[len("AIDOC_"):], _v)

__version__ = "0.1.1"
