# utils/firebase_logging.py

import uuid
import datetime

import firebase_admin
from firebase_admin import credentials, db, storage

_LOGGER = None  # kept for backward compatibility, but we now prefer session_state


def _get_or_create_session_id(st):
    key = "_session_id"
    if key not in st.session_state:
        st.session_state[key] = uuid.uuid4().hex
    return st.session_state[key]


class FirebaseLogger:
    def __init__(self, st, run_context=None):
        self.st = st
        self.run_ref = None

        if "firebase" in st.secrets:
            svc_dict = dict(st.secrets["firebase"]["service_account"])
            bucket = st.secrets.get("firebase", {}).get("storage_bucket") or f"{svc_dict['project_id']}.appspot.com"

            if not firebase_admin._apps:
                cred = credentials.Certificate(svc_dict)
                app = firebase_admin.initialize_app(cred, {
                    "databaseURL": st.secrets["firebase"]["database_url"],  # NEW
                    "storageBucket": bucket,
                })
            else:
                app = firebase_admin.get_app()

            # Use our own per-session UUID instead of st.session_id
            self.session_id = _get_or_create_session_id(st)
            self.db = db.reference(f"logs/streamlit/{self.session_id}")

            # Prepare subpaths and storage
            self.events_ref = self.db.child("events")
            self.bucket = storage.bucket(app=app)

            # Optionally record run context/start
            if run_context:
                self.run_ref = self.db.child("runs").push({
                    "context": run_context,
                    "started_at": datetime.datetime.utcnow().isoformat() + "Z",
                })

    def info(self, msg, **extra):
        self._event("INFO", msg, extra)

    def warn(self, msg, **extra):
        self._event("WARN", msg, extra)

    def error(self, msg, **extra):
        self._event("ERROR", msg, extra)

    def _event(self, level, message, extra=None):
        payload = {
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
            "level": level,
            "message": message,
        }
        if extra:
            payload.update(extra)
        # Realtime Database: append using push(), not add()
        self.events_ref.push(payload)

    def upload_screenshot(self, png_bytes, name_hint="screenshot"):
        path = f"runs/{self.session_id}/screens/{uuid.uuid4()}_{name_hint}.png"
        blob = self.bucket.blob(path)
        blob.upload_from_string(png_bytes, content_type="image/png")
        return f"gs://{self.bucket.name}/{path}"

    def end_run(self, status="completed", summary=None):
        if self.run_ref:
            self.run_ref.update({
                "ended_at": datetime.datetime.utcnow().isoformat() + "Z",
                "status": status,
                "summary": summary or {},
            })


def ensure_logger(st, run_context=None):
    # Prefer a per-session singleton stored in session_state
    if "fb_logger" not in st.session_state:
        st.session_state["fb_logger"] = FirebaseLogger(st, run_context=run_context)
    elif run_context and st.session_state["fb_logger"].run_ref:
        st.session_state["fb_logger"].run_ref.update({"context": run_context})
    return st.session_state["fb_logger"]


def get_logger(st):
    # Convenience accessor if needed elsewhere
    return st.session_state.get("fb_logger")


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
    return originals
