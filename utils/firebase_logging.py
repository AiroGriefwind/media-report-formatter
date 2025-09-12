# utils/firebase_logging.py
import os, json, uuid, datetime
import firebase_admin
from firebase_admin import credentials, firestore, storage

_LOGGER = None  # module-level singleton

class FirebaseLogger:
    def __init__(self, st, run_context=None):
        # init from Streamlit secrets
        svc = st.secrets["firebase"]["service_account"]
        bucket = st.secrets.get("firebase", {}).get("storage_bucket") or f"{svc['project_id']}.appspot.com"
        if not firebase_admin._apps:
            cred = credentials.Certificate(svc)
            app = firebase_admin.initialize_app(cred, {"storageBucket": bucket})
        else:
            app = firebase_admin.get_app()
        self.db = firestore.client(app)
        self.bucket = storage.bucket(app=app)
        self.run_id = str(uuid.uuid4())
        self.run_ref = self.db.collection("runs").document(self.run_id)
        self.events_ref = self.run_ref.collection("events")
        self.run_ref.set({"started_at": datetime.datetime.utcnow().isoformat() + "Z",
                          "status": "running", "context": run_context or {}}, merge=True)

    def info(self, msg, **extra): self._event("INFO", msg, extra)
    def warn(self, msg, **extra): self._event("WARN", msg, extra)
    def error(self, msg, **extra): self._event("ERROR", msg, extra)
    def _event(self, level, message, extra=None):
        payload = {"ts": datetime.datetime.utcnow().isoformat() + "Z", "level": level, "message": message}
        if extra: payload.update(extra)
        self.events_ref.add(payload)
    def upload_screenshot(self, png_bytes, name_hint="screenshot"):
        path = f"runs/{self.run_id}/screens/{uuid.uuid4()}_{name_hint}.png"
        blob = self.bucket.blob(path)
        blob.upload_from_string(png_bytes, content_type="image/png")
        return f"gs://{self.bucket.name}/{path}"
    def end_run(self, status="completed", summary=None):
        self.run_ref.set({"ended_at": datetime.datetime.utcnow().isoformat() + "Z",
                          "status": status, "summary": summary or {}}, merge=True)

def ensure_logger(st, run_context=None):
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = FirebaseLogger(st, run_context=run_context)
    elif run_context:
        _LOGGER.run_ref.set({"context": run_context}, merge=True)
    return _LOGGER

def get_logger():
    return _LOGGER

def patch_streamlit_logging(st):
    fb = ensure_logger(st)
    originals = {}
    for name in ["write", "info", "warning", "error", "success", "code"]:
        orig = getattr(st, name)
        originals[name] = orig
        def make_logged(fn, fname=name):
            def inner(*args, **kwargs):
                msg = " ".join(str(a) for a in args) if args else ""
                if fname in ["error", "warning"]:
                    fb.error(msg)
                else:
                    fb.info(msg)
                return fn(*args, **kwargs)
            return inner
        setattr(st, name, make_logged(orig))
    # optional: return originals if you ever want to restore them
    return originals
