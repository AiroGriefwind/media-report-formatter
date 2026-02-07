# utils/firebase_logging.py

import uuid
import datetime as dt
import pytz
import os
import json
import tempfile  # ✅ 新增
import firebase_admin
from firebase_admin import credentials, db, storage
from datetime import datetime
import time

HKT = pytz.timezone('Asia/Hong_Kong')
TODAY = datetime.now(HKT).strftime("%Y%m%d")

_LOGGER = None  # kept for backward compatibility, but we now prefer session_state

def _today_hkt_str() -> str:
    # Always compute "today" at call time to avoid stale date after midnight
    return dt.datetime.now(HKT).strftime("%Y%m%d")

def _date_folder(base_folder: str) -> str:
    safe_base = (base_folder or "international_news").strip().strip("/")
    return f"{safe_base}/{_today_hkt_str()}"

def _get_or_create_session_id(st):
    key = "_session_id"
    if key not in st.session_state:
        st.session_state[key] = uuid.uuid4().hex
    return st.session_state[key]


class FirebaseLogger:
    def __init__(self, st, run_context=None):

        self.st = st
        self.bucket = None      # Will be set if Firebase enabled
        self.events_ref = None  # Points to runs/<run_id>/events
        self.screens_ref = None # Points to runs/<run_id>/screens
        self.run_ref = None     # Points to this run's root
        self.run_id = None
        self.session_id = _get_or_create_session_id(st)
        self._last_log_upload_ts = 0.0

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

            # Use current time in HKT for run_id (for local files/folders)
            now_hkt = dt.datetime.now(HKT)
            self.run_id = now_hkt.strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
            self.local_log_events = []
            # Pointer to this run:
            self.run_ref = self.db.child("runs").child(self.run_id)
            self.events_ref = self.run_ref.child("events")
            self.screens_ref = self.run_ref.child("screens")
            # Mark start/meta fields
            meta = {
                "started_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            }
            if run_context:
                meta["context"] = run_context
            self.run_ref.update(meta)

            # Set up gs bucket as before
            self.bucket = storage.bucket(app=app)

    def run_storage_dir(self) -> str:
        return f"runs/{_today_hkt_str()}/{self.run_id}"

    def info(self, msg, **extra):
        self._event("INFO", msg, extra)

    def warn(self, msg, **extra):
        self._event("WARN", msg, extra)

    def error(self, msg, **extra):
        self._event("ERROR", msg, extra)

    def _event(self, level, message, extra=None):
        # Only log to local, NOT Firebase DB
        payload = {
                    "ts": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                    "level": level,
                    "message": message
                }
        if extra:
            payload.update(extra)
        self.local_log_events.append(payload)
        # DO NOT: self.events_ref.push(payload)

    def save_json_to_date_folder(self, data, filename, base_folder="international_news"):
        """Save JSON under date-based folder."""
        folderpath = _date_folder(base_folder)
        remotepath = f"{folderpath}/{filename}"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmppath = tmp.name

        gsurl = self.upload_file_to_firebase(tmppath, remotepath)
        os.unlink(tmppath)
        return gsurl

    def load_json_from_date_folder(self, filename, default=None, base_folder="international_news"):
        """Load JSON from date-based folder."""
        folderpath = _date_folder(base_folder)
        remotepath = f"{folderpath}/{filename}"

        try:
            blob = self.bucket.blob(remotepath)
            if blob.exists():
                content = blob.download_as_string().decode("utf-8")
                return json.loads(content)
        except Exception:
            pass
        return default

    def upload_file_to_firebase(self, local_fp, remote_path):
        blob = self.bucket.blob(remote_path)
        blob.upload_from_filename(local_fp)
        return f"gs://{self.bucket.name}/{remote_path}"

    def save_final_docx_to_date_folder(self, articlesdata, filename, base_folder="international_news"):
        """Save DOCX under date-based folder."""
        import tempfile
        from utils.international_news_utils import create_international_news_report

        folderpath = _date_folder(base_folder)
        remotepath = f"{folderpath}/{filename}"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            create_international_news_report(
                articles_data=articlesdata,
                output_path=tmp.name,
                st_module=None
            )
            tmppath = tmp.name

        gsurl = self.upload_file_to_firebase(tmppath, remotepath)
        os.unlink(tmppath)
        return gsurl

    def save_final_docx_bytes_to_date_folder(self, docxbytes: bytes, filename: str, base_folder="international_news"):
        """Save DOCX bytes under date-based folder."""
        folderpath = _date_folder(base_folder)
        remotepath = f"{folderpath}/{filename}"

        try:
            blob = self.bucket.blob(remotepath)
            blob.upload_from_string(
                docxbytes,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            return f"gs://{self.bucket.name}/{remotepath}"
        except Exception:
            return None


    def load_final_docx_from_date_folder(self, filename, base_folder="international_news"):
        """Load DOCX bytes from date-based folder."""
        folderpath = _date_folder(base_folder)
        remotepath = f"{folderpath}/{filename}"

        try:
            blob = self.bucket.blob(remotepath)
            if blob.exists():
                return blob.download_as_bytes()
        except Exception:
            pass
        return None

    def export_log_json(self, log_dir):
        # Write file locally as before
        os.makedirs(log_dir, exist_ok=True)
        fp = os.path.join(log_dir, f"{self.run_id}_logs.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(self.local_log_events, f, ensure_ascii=False, indent=2)
        return fp

    def upload_log_events_json(self, filename="streamlit_logs.json"):
        if not self.bucket:
            return None
        payload = json.dumps(self.local_log_events, ensure_ascii=False, indent=2)
        run_dir = self.run_storage_dir()
        remote_path = f"{run_dir}/logs/{filename}"
        blob = self.bucket.blob(remote_path)
        blob.upload_from_string(payload, content_type="application/json")
        return f"gs://{self.bucket.name}/{remote_path}"

    def flush_logs_to_firebase(self, force=False, min_interval=8):
        if not self.bucket:
            return None
        now = time.time()
        if not force and (now - self._last_log_upload_ts) < min_interval:
            return None
        self._last_log_upload_ts = now
        try:
            return self.upload_log_events_json()
        except Exception:
            return None

    def upload_screenshot_bytes(self, img_bytes, filename=None, subdir="screens"):
        if not self.bucket:
            return None
        ts = dt.datetime.now(HKT).strftime("%Y%m%d_%H%M%S")
        fname = filename or f"{ts}_{uuid.uuid4().hex[:6]}.png"
        run_dir = self.run_storage_dir()
        remote_path = f"{run_dir}/{subdir}/{fname}"
        blob = self.bucket.blob(remote_path)
        blob.upload_from_string(img_bytes, content_type="image/png")
        return f"gs://{self.bucket.name}/{remote_path}"

    def upload_scrcap_to_firebase(self, local_fp, name_hint="screenshot"):
        # Upload individual screenshot file
        run_screens_dir = f"{self.run_storage_dir()}/screens/"
        remote_path = run_screens_dir + os.path.basename(local_fp)
        return self.upload_file_to_firebase(local_fp, remote_path)

    def upload_json_to_firebase(self, json_fp):
        run_dir = f"{self.run_storage_dir()}/logs/"
        remote_path = run_dir + os.path.basename(json_fp)
        return self.upload_file_to_firebase(json_fp, remote_path)


    def end_run(self, status="completed", summary=None, upload_logs=True):
        if self.run_ref:
            self.run_ref.update({
                "ended_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                 "status": status,
                 "summary": summary or {},
             })
        if upload_logs:
            try:
                self.upload_log_events_json()
            except Exception:
                pass


class FirebaseLoggerCLI:
    """
    A minimal Firebase logger for non-Streamlit contexts (e.g. cron/CLI scripts).

    It focuses on Firebase Storage (gs://) uploads/reads under date folders and keeps
    an in-memory event list for optional local export.

    Environment variables supported:
      - FIREBASE_SERVICE_ACCOUNT_JSON: JSON string of a service account
      - FIREBASE_SERVICE_ACCOUNT_FILE: path to a service account JSON file
      - GOOGLE_APPLICATION_CREDENTIALS: standard ADC path (fallback)
      - FIREBASE_DATABASE_URL: optional (only needed if you want RTDB logging)
      - FIREBASE_STORAGE_BUCKET: optional; defaults to "<project_id>.appspot.com" when available
    """

    def __init__(self, run_context=None, session_id=None):
        self.st = None
        self.bucket = None
        self.events_ref = None
        self.screens_ref = None
        self.run_ref = None
        self.session_id = session_id or uuid.uuid4().hex

        now_hkt = dt.datetime.now(HKT)
        self.run_id = now_hkt.strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.local_log_events = []
        self._init_firebase_from_env(run_context=run_context)
        self._last_log_upload_ts = 0.0

    def _init_firebase_from_env(self, run_context=None):
        svc_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        svc_file = os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE")
        db_url = os.getenv("FIREBASE_DATABASE_URL")

        # Prefer explicit bucket; otherwise infer from project_id when possible.
        bucket = os.getenv("FIREBASE_STORAGE_BUCKET")

        # Credentials
        cred = None
        project_id = None
        if svc_json:
            try:
                svc_dict = json.loads(svc_json)
            except Exception as e:
                raise RuntimeError("Invalid FIREBASE_SERVICE_ACCOUNT_JSON (must be valid JSON)") from e
            project_id = svc_dict.get("project_id")
            cred = credentials.Certificate(svc_dict)
        elif svc_file:
            if not os.path.exists(svc_file):
                raise RuntimeError(f"FIREBASE_SERVICE_ACCOUNT_FILE not found: {svc_file}")
            try:
                with open(svc_file, "r", encoding="utf-8") as f:
                    svc_dict = json.load(f)
                project_id = svc_dict.get("project_id")
            except Exception:
                # If reading fails, still try Certificate(file) directly
                svc_dict = None
            cred = credentials.Certificate(svc_file)
        else:
            # Fallback to Application Default Credentials if configured
            cred = credentials.ApplicationDefault()

        if not bucket and project_id:
            bucket = f"{project_id}.appspot.com"

        if not bucket:
            raise RuntimeError(
                "Firebase Storage bucket not configured. Set FIREBASE_STORAGE_BUCKET or provide a "
                "service account with project_id via FIREBASE_SERVICE_ACCOUNT_JSON/FIREBASE_SERVICE_ACCOUNT_FILE."
            )

        options = {"storageBucket": bucket}
        if db_url:
            options["databaseURL"] = db_url

        if not firebase_admin._apps:
            app = firebase_admin.initialize_app(cred, options)
        else:
            app = firebase_admin.get_app()

        self.bucket = storage.bucket(app=app)

        # Optional RTDB run logging (only if db_url is configured and app was initialized with it)
        if db_url:
            try:
                self.db = db.reference(f"logs/cli/{self.session_id}")
                self.run_ref = self.db.child("runs").child(self.run_id)
                self.events_ref = self.run_ref.child("events")
                self.screens_ref = self.run_ref.child("screens")
                meta = {
                    "started_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
                }
                if run_context:
                    meta["context"] = run_context
                self.run_ref.update(meta)
            except Exception:
                # Storage still works; ignore RTDB failures.
                self.db = None
                self.run_ref = None
                self.events_ref = None
                self.screens_ref = None

    def run_storage_dir(self) -> str:
        return f"runs/{_today_hkt_str()}/{self.run_id}"

    def info(self, msg, **extra):
        self._event("INFO", msg, extra)

    def warn(self, msg, **extra):
        self._event("WARN", msg, extra)

    def error(self, msg, **extra):
        self._event("ERROR", msg, extra)

    def _event(self, level, message, extra=None):
        payload = {
            "ts": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": level,
            "message": message,
        }
        if extra:
            payload.update(extra)
        self.local_log_events.append(payload)
        if self.events_ref:
            try:
                self.events_ref.push(payload)
            except Exception:
                pass

    def save_json_to_date_folder(self, data, filename):
        if not self.bucket:
            raise RuntimeError("Firebase Storage is not initialized (bucket is None).")
        folderpath = f"international_news/{_today_hkt_str()}"
        remotepath = f"{folderpath}/{filename}"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmppath = tmp.name

        gsurl = self.upload_file_to_firebase(tmppath, remotepath)
        os.unlink(tmppath)
        return gsurl

    def load_json_from_date_folder(self, filename, default=None):
        if not self.bucket:
            return default
        folderpath = f"international_news/{_today_hkt_str()}"
        remotepath = f"{folderpath}/{filename}"

        try:
            blob = self.bucket.blob(remotepath)
            if blob.exists():
                content = blob.download_as_string().decode("utf-8")
                return json.loads(content)
        except Exception:
            pass
        return default

    def upload_file_to_firebase(self, local_fp, remote_path):
        if not self.bucket:
            raise RuntimeError("Firebase Storage is not initialized (bucket is None).")
        blob = self.bucket.blob(remote_path)
        blob.upload_from_filename(local_fp)
        return f"gs://{self.bucket.name}/{remote_path}"

    def export_log_json(self, log_dir):
        os.makedirs(log_dir, exist_ok=True)
        fp = os.path.join(log_dir, f"{self.run_id}_logs.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(self.local_log_events, f, ensure_ascii=False, indent=2)
        return fp

    def upload_log_events_json(self, filename="cli_logs.json"):
        if not self.bucket:
            return None
        payload = json.dumps(self.local_log_events, ensure_ascii=False, indent=2)
        run_dir = self.run_storage_dir()
        remote_path = f"{run_dir}/logs/{filename}"
        blob = self.bucket.blob(remote_path)
        blob.upload_from_string(payload, content_type="application/json")
        return f"gs://{self.bucket.name}/{remote_path}"

    def end_run(self, status="completed", summary=None, upload_logs=True):
        if self.run_ref:
            try:
                self.run_ref.update({
                    "ended_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                    "status": status,
                    "summary": summary or {},
                })
            except Exception:
                pass
        if upload_logs:
            try:
                self.upload_log_events_json()
            except Exception:
                pass


def ensure_logger(st, run_context=None):
    # Prefer a per-session singleton stored in session_state
    if "fb_logger" not in st.session_state:
        st.session_state["fb_logger"] = FirebaseLogger(st, run_context=run_context)
    elif run_context and st.session_state["fb_logger"].run_ref:
        st.session_state["fb_logger"].run_ref.update({"context": run_context})
    return st.session_state["fb_logger"]


def create_cli_logger(run_context=None) -> FirebaseLoggerCLI:
    """
    Create a Firebase logger for CLI/cron usage (no Streamlit dependency).
    """
    return FirebaseLoggerCLI(run_context=run_context)


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
                try:
                    fb.flush_logs_to_firebase()
                except Exception:
                    pass
                return fn(*args, **kwargs)
            return inner

        setattr(st, name, make_logged(orig))
    return originals

