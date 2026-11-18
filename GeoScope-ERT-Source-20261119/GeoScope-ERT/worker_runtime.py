"""Persistent display initialization cache, separate from numerical job data."""
import hashlib
import os
import sys
from pathlib import Path


def configure_matplotlib_cache(root=None):
    """Reuse fonts for this installation without sharing solver inputs/results.

    PyInstaller's matplotlib hook uses a new temporary cache each launch. Our
    onedir distribution has stable bundled font paths, so it can safely reuse a
    cache keyed by Python and the bundle location. Onefile extraction paths get
    separate identities. If the location cannot be written, retain the existing
    Matplotlib fallback rather than preventing the application from starting.
    """
    if root is None:
        root=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
    root=Path(root).resolve()
    bundle=Path(getattr(sys,'_MEIPASS',root)).resolve()
    identity=hashlib.sha256((str(root).casefold()+'|'+str(bundle).casefold()).encode('utf-8')).hexdigest()[:12]
    tag=sys.implementation.cache_tag or 'python'
    cache=root/'.cache'/'matplotlib'/(tag+'-'+identity)
    try:
        cache.mkdir(parents=True,exist_ok=True)
        # Probe writability: an existing read-only directory also needs fallback.
        import tempfile
        with tempfile.TemporaryFile(dir=cache):pass
    except OSError:
        return None
    os.environ['MPLCONFIGDIR']=str(cache)
    return cache
