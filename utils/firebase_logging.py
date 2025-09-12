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
        import uuid, datetime

        self.st = st
        self.bucket = None      # Will be set if Firebase enabled
        self.events_ref = None  # Points to runs/<run_id>/events
        self.screens_ref = None # Points to runs/<run_id>/screens
        self.run_ref = None     # Points to this run's root
        self.run_id = None
        self.session_id = _get_or_create_session_id(st)

        if "firebase" in st.secrets:
            svc_dict = dict(st.secrets["firebase"]["service_account"])
            bucket = st.secrets.get("firebase", {}).get("storage_bucket") or f"{svc_dict['project_id']}.appspot.com"
            db_url = st.secrets["firebase"]["database_url"]  # Ensure you have this in secrets.toml

            # Initialize firebase only once per process
            if not firebase_admin._apps:
                cred = credentials.Certificate(svc_dict)
                app = firebase_admin.initialize_app(cred, {
                    "databaseURL": db_url,
                    "storageBucket": bucket,
                })
            else:
                app = firebase_admin.get_app()

            # Reference: logs/streamlit/<session_id>
            self.db = db.reference(f"logs/streamlit/{self.session_id}")

            # --- START OF RUN-ISOLATION PATCH ---
            # Create a new, sortable run_id: e.g. 20250912T161100_ab1c2d
            self.run_id = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
            # Pointer to this run:
            self.run_ref = self.db.child("runs").child(self.run_id)
            self.events_ref = self.run_ref.child("events")
            self.screens_ref = self.run_ref.child("screens")
            # Mark start/meta fields
            meta = {
                "started_at": datetime.datetime.utcnow().isoformat() + "Z"
            }
            if run_context:
                meta["context"] = run_context
            self.run_ref.update(meta)

            # Set up gs bucket as before
            self.bucket = storage.bucket(app=app)


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
        self.events_ref.push(payload)

    def upload_screenshot(self, png_bytes, name_hint="screenshot"):
        path = f"runs/{self.session_id}/{self.run_id}/screens/{uuid.uuid4()}_{name_hint}.png"
        blob = self.bucket.blob(path)
        blob.upload_from_string(png_bytes, content_type="image/png")
        gs_url = f"gs://{self.bucket.name}/{path}"
        meta = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "label": name_hint,
            "gs_url": gs_url,
        }
        self.screens_ref.push(meta)
        return gs_url


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
