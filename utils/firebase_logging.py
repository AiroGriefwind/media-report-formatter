# utils/firebase_logging.py
import os, json, uuid, datetime
import firebase_admin
from firebase_admin import credentials, db, storage
import logging

_LOGGER = None  # module-level singleton

class FirebaseLogger:
    def __init__(self, st, run_context=None):
        self.st = st
        self.run_ref = None
        
        if "firebase" in st.secrets:
            # FIX: Convert the Streamlit secrets object to a standard Python dictionary.
            # The Firebase SDK expects a `dict`, but `st.secrets` returns a proxy object.
            svc_dict = dict(st.secrets["firebase"]["service_account"])
            
            # Use the newly created dictionary to get the project_id
            bucket = st.secrets.get("firebase", {}).get("storage_bucket") or f"{svc_dict['project_id']}.appspot.com"
            
            if not firebase_admin._apps:
                # Pass the standard dictionary to the Certificate constructor
                cred = credentials.Certificate(svc_dict)
                app = firebase_admin.initialize_app(cred, {"storageBucket": bucket})
            else:
                app = firebase_admin.get_app()
                
            self.db = db.reference(f"logs/streamlit/{st.session_id}")
            self.bucket = storage.bucket(app=app)
            
            if run_context:
                self.run_ref = self.db.push({"context": run_context})

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
