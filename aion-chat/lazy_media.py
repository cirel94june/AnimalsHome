"""Load native camera/audio arrays only when an operation actually uses them."""
from functools import lru_cache
from importlib import import_module
from threading import RLock


class _LazyModule:
    def __init__(self, name):
        self._name = name
        self._loaded = None
        self._lock = RLock()

    def __getattr__(self, attribute):
        with self._lock:
            if self._loaded is None:
                # A failed import remains retryable; publish only a complete
                # module, never a partially initialized native extension.
                self._loaded = import_module(self._name)
            module = self._loaded
        return getattr(module, attribute)


@lru_cache(maxsize=2)
def media_module(name):
    if name not in {'cv2','numpy'}:
        raise ValueError('unsupported media module')
    return _LazyModule(name)
