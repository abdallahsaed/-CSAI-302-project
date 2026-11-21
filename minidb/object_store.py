"""Atomic local object storage with optional authenticated Fernet encryption."""
import hashlib, os, tempfile

class IdentityCodec:
    """Explicit plaintext codec; use FernetCodec when confidentiality is required."""
    def encrypt(self, data): return data
    def decrypt(self, data): return data

class FernetCodec:
    """AES-128-CBC + HMAC authenticated encryption via cryptography's Fernet."""
    def __init__(self, key):
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise RuntimeError("Fernet encryption requires: pip install cryptography") from exc
        if isinstance(key, str): key = key.encode("ascii")
        self._fernet = Fernet(key)
    @staticmethod
    def generate_key():
        from cryptography.fernet import Fernet
        return Fernet.generate_key()
    def encrypt(self, data): return self._fernet.encrypt(bytes(data))
    def decrypt(self, data): return self._fernet.decrypt(bytes(data))

class ObjectStore:
    def __init__(self, root, codec=None):
        self.root = os.path.join(root, "objects"); os.makedirs(self.root, exist_ok=True); self.codec = codec or IdentityCodec()
    @staticmethod
    def _safe_name(key):
        if not isinstance(key, str) or not key: raise ValueError("object key must be a non-empty string")
        return hashlib.sha256(key.encode("utf-8")).hexdigest()
    def _path(self, key): return os.path.join(self.root, self._safe_name(key) + ".obj")
    def put(self, key, data):
        if not isinstance(data, (bytes, bytearray, memoryview)): raise TypeError("object data must be bytes-like")
        payload = self.codec.encrypt(bytes(data)); target = self._path(key)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as f: f.write(payload); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return key
    def get(self, key):
        with open(self._path(key), "rb") as f: payload = f.read()
        return self.codec.decrypt(payload)
    def delete(self, key):
        path = self._path(key)
        if not os.path.exists(path): return False
        os.remove(path); return True
    def exists(self, key): return os.path.exists(self._path(key))
