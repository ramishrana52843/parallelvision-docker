"""
application.py — ParallelVision, cloud edition.

(Named application.py because Elastic Beanstalk's Python platform looks for a
WSGI callable called `application` in this file by default.)

Routes
  GET  /                    main UI (login required)
  POST /process             run the parallel job; optionally save results to S3 + RDS
  GET  /gallery             the logged-in user's saved images (thumbnails from S3)
  POST /gallery/<id>/delete delete one saved image (S3 objects + DB row)
  GET  /status              RDS + S3 connectivity check (login required)
  GET  /health              light health check for load balancers (no login)
  /register /login /logout /verify   see auth.py
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, url_for)
from PIL import Image as PILImage, ImageOps
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.utils import secure_filename

import storage
from auth import bp as auth_bp, login_required
from config import Config
from models import db, Image
from processor import (run_parallel, run_sequential, get_omp_threads,
                       safe_worker_count, JobTimeout)

# PIL format name -> (file extension, content type)
ALLOWED_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG":  ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "GIF":  ("gif", "image/gif"),
    "BMP":  ("bmp", "image/bmp"),
}
MAX_MEGAPIXELS = 40          # per image; bigger ones can exhaust a small server
THUMB_MAX_SIDE = 480         # gallery thumbnails
PREVIEW_MAX_SIDE = 800       # image shown in the results panel
TIMING_FIELDS = ("image_name", "pid", "total_ms", "filter_times", "aug_times", "out_w", "out_h")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _make_small(data: bytes, max_side: int, fix_orientation: bool = False) -> bytes:
    """Small JPEG. draft() lets Pillow decode huge JPEGs at reduced size (very fast)."""
    img = PILImage.open(io.BytesIO(data))
    img.draft("RGB", (max_side * 2, max_side * 2))
    if fix_orientation:
        img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side), PILImage.Resampling.BILINEAR, reducing_gap=2.0)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=78)
    return out.getvalue()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["EMAIL_VERIFICATION"] = Config.email_verification_enabled()
    app.db_ready = False
    app._db_lock = threading.Lock()
    app._status_cache = {"t": 0.0, "value": None}

    logging.basicConfig(level=logging.INFO)
    if app.config["SECRET_KEY"] == "dev-only-change-me":
        app.logger.warning("SECRET_KEY is the insecure default. Set the SECRET_KEY env var.")
    if not storage.enabled(app):
        app.logger.warning("S3_BUCKET is not set: results can be processed but not saved.")

    db.init_app(app)

    # ── Error handling ───────────────────────────────────────────────────────

    def wants_json() -> bool:
        return (request.path.startswith(("/process", "/status"))
                or request.accept_mimetypes.best == "application/json")

    def error_response(status: int, message: str, ref: str | None = None):
        if wants_json():
            body = {"error": message}
            if ref:
                body["ref"] = ref
            return jsonify(body), status
        return render_template("error.html", status=status, message=message, ref=ref), status

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_e):
        mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return error_response(413, f"That upload is too large (limit {mb} MB per job). "
                                   "Use fewer or smaller images.")

    @app.errorhandler(SQLAlchemyError)
    def database_error(exc):
        db.session.rollback()
        app.logger.error("Database error on %s %s: %s", request.method, request.path, exc)
        return error_response(503, "The database is temporarily unavailable. "
                                   "Please try again in a few seconds.")

    @app.errorhandler(HTTPException)
    def http_error(exc):
        messages = {404: "That page does not exist.", 405: "That action is not allowed here.",
                    400: exc.description or "Bad request."}
        return error_response(exc.code or 500, messages.get(exc.code, exc.description or "Request failed."))

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        ref = uuid.uuid4().hex[:8]
        app.logger.exception("Unhandled error [ref %s] on %s %s", ref, request.method, request.path)
        db.session.rollback()
        return error_response(500, "Something went wrong on the server. It has been logged.", ref)

    # ── Database readiness ───────────────────────────────────────────────────
    # Tables are created on first successful contact with the database. If RDS
    # was unreachable at startup (security group, still booting, ...) the app
    # keeps retrying on each request instead of needing a manual restart.

    def ensure_db() -> bool:
        if app.db_ready:
            return True
        with app._db_lock:
            if app.db_ready:
                return True
            try:
                db.create_all()
                app.db_ready = True
                app.logger.info("Database ready.")
            except Exception as exc:
                db.session.rollback()
                app.logger.error("Database not ready yet: %s", exc)
        return app.db_ready

    with app.app_context():
        ensure_db()

    @app.before_request
    def gate_on_database():
        if request.endpoint in ("health", "static"):
            return None
        if not ensure_db():
            return error_response(503, "The database is not reachable yet. "
                                       "Please try again in a moment.")
        return None

    app.register_blueprint(auth_bp)

    # ── UI ───────────────────────────────────────────────────────────────────

    @app.route("/")
    @login_required
    def index():
        return render_template("index.html", user_email=g.user.email,
                               s3_enabled=storage.enabled(app),
                               bucket=app.config["S3_BUCKET"], region=app.config["AWS_REGION"])

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "omp_threads": get_omp_threads()})

    @app.route("/status")
    @login_required
    def status():
        cache = app._status_cache
        if cache["value"] and time.time() - cache["t"] < 30:
            return jsonify(cache["value"])
        out = {"database": {"ok": False}, "storage": {"ok": False, "enabled": storage.enabled(app)},
               "email_verification": app.config["EMAIL_VERIFICATION"]}
        try:
            db.session.execute(text("SELECT 1"))
            out["database"] = {"ok": True, "engine": db.engine.dialect.name}
        except Exception as exc:
            db.session.rollback()
            app.logger.error("Status: database check failed: %s", exc)
        if storage.enabled(app):
            try:
                storage.ping(app)
                out["storage"].update(ok=True, bucket=app.config["S3_BUCKET"], region=app.config["AWS_REGION"])
            except Exception as exc:
                app.logger.error("Status: S3 check failed: %s", exc)
        cache.update(t=time.time(), value=out)
        return jsonify(out)

    # ── Processing ───────────────────────────────────────────────────────────

    @app.route("/process", methods=["POST"])
    @login_required
    def process():
        """
        Form-data fields:
          images[] : uploaded image files (1..N)
          config   : JSON with filters, augmentations, n_workers,
                     run_sequential, save_to_cloud
        """
        files = request.files.getlist("images[]")
        try:
            config = json.loads(request.form.get("config", "{}"))
            if not isinstance(config, dict):
                raise ValueError
        except ValueError:
            return jsonify({"error": "The job settings were not valid. Reload the page and try again."}), 400

        if not files:
            return jsonify({"error": "No images were uploaded."}), 400
        if len(files) > app.config["MAX_IMAGES_PER_JOB"]:
            return jsonify({"error": f"Maximum {app.config['MAX_IMAGES_PER_JOB']} images per job."}), 400

        filters = config.get("filters", [])
        augmentations = config.get("augmentations", [])
        for item in list(filters) + list(augmentations):
            if not isinstance(item, dict) or not item.get("name"):
                return jsonify({"error": "The filter settings were not valid. Reload the page and try again."}), 400
        try:
            requested_workers = max(1, min(int(config.get("n_workers", 4)), 16))
        except (TypeError, ValueError):
            requested_workers = 4
        do_sequential = bool(config.get("run_sequential", True))
        do_save = bool(config.get("save_to_cloud", True)) and storage.enabled(app)

        tasks, raw_files, max_pixels = [], [], 0
        for f in files:
            raw = f.read()
            try:
                probe = PILImage.open(io.BytesIO(raw))
                fmt, (w, h) = probe.format, probe.size
                probe.verify()
            except Exception:
                return jsonify({"error": f"'{f.filename}' is not a valid image file."}), 400
            if fmt not in ALLOWED_FORMATS:
                return jsonify({"error": f"'{f.filename}': {fmt} images are not supported "
                                         "(use JPEG, PNG, WEBP, GIF or BMP)."}), 400
            if w * h > MAX_MEGAPIXELS * 1_000_000:
                return jsonify({"error": f"'{f.filename}' is {w}×{h} px. The limit is "
                                         f"{MAX_MEGAPIXELS} megapixels per image."}), 400
            max_pixels = max(max_pixels, w * h)
            name = secure_filename(f.filename or "") or "image"
            raw_files.append((raw, fmt, name))
            tasks.append({"image_bytes": raw, "filters": filters,
                          "augmentations": augmentations, "image_name": name})

        n_workers = safe_worker_count(requested_workers, max_pixels)

        try:
            par = run_parallel(tasks, n_workers)
            seq = run_sequential(tasks) if do_sequential else None
        except JobTimeout:
            app.logger.error("Job timed out (%d images, %d workers)", len(tasks), n_workers)
            return jsonify({"error": "Processing took too long and was stopped. "
                                     "Try fewer or smaller images, or fewer workers."}), 504
        except MemoryError:
            return jsonify({"error": "The server ran out of memory. Try fewer or smaller images."}), 507
        except Exception:
            ref = uuid.uuid4().hex[:8]
            app.logger.exception("Processing failed [ref %s]", ref)
            return jsonify({"error": "Processing failed on the server. It has been logged.", "ref": ref}), 500

        speedup = round(seq["wall_ms"] / par["wall_ms"], 2) if seq and par["wall_ms"] > 0 else None

        # ── Save to S3 (bytes) + RDS (metadata), after timing is measured ──
        saved, save_error, save_ms = {}, None, None
        if do_save:
            t0 = time.perf_counter()
            ops = ",".join([x["name"] for x in filters] + [x["name"] for x in augmentations])
            saved, save_error = _save_all(app, g.user.id, raw_files, par["results"], ops)
            save_ms = round((time.perf_counter() - t0) * 1000, 1)

        # ── Build a SMALL response: previews + timings, never the full-size images ──
        out_results = []
        for i, res in enumerate(par["results"]):
            item = {k: res[k] for k in TIMING_FIELDS}
            info = saved.get(i)
            if info:
                stem = os.path.splitext(res["image_name"])[0] or "image"
                item["preview_b64"] = _b64(info["preview"])
                item["s3_key"] = info["result_key"]
                item["download_url"] = storage.presigned_url(app, info["result_key"], f"processed_{stem}.jpg")
            else:
                # Not stored in S3, so the browser needs the full-size image for its Download button.
                item["result_b64"] = _b64(res["result_jpeg"])
            out_results.append(item)

        return jsonify({
            "parallel": {"wall_ms": par["wall_ms"], "mode": par["mode"],
                         "workers": par["workers"], "results": out_results},
            # the sequential run is only used for timings, so no image data goes back
            "sequential": None if not seq else {
                "wall_ms": seq["wall_ms"], "mode": seq["mode"], "workers": seq["workers"],
                "results": [{k: r[k] for k in TIMING_FIELDS} for r in seq["results"]]},
            "speedup": speedup,
            "omp_threads": get_omp_threads(),
            "workers_requested": requested_workers,
            "saved": len(saved),
            "save_ms": save_ms,
            "save_error": save_error,
            "storage": {"bucket": app.config["S3_BUCKET"], "region": app.config["AWS_REGION"]} if saved else None,
        })

    # ── Gallery ──────────────────────────────────────────────────────────────

    @app.route("/gallery")
    @login_required
    def gallery():
        items = []
        if storage.enabled(app):
            rows = (Image.query.filter_by(user_id=g.user.id)
                    .order_by(Image.created_at.desc()).limit(48).all())
            bucket = app.config["S3_BUCKET"]
            for r in rows:
                stem = os.path.splitext(r.display_name)[0] or "image"
                items.append({
                    "id": r.id,
                    "name": r.display_name,
                    "ops": r.operations,
                    "size": f"{r.width}×{r.height}" if r.width else "",
                    "ms": r.process_ms,
                    "created": r.created_at.strftime("%Y-%m-%d %H:%M UTC"),
                    "s3_uri": f"s3://{bucket}/{r.result_key}",
                    "original_thumb": storage.presigned_url(app, storage.thumb_key(r.original_key)),
                    "result_thumb": storage.presigned_url(app, storage.thumb_key(r.result_key)),
                    "original_url": storage.presigned_url(app, r.original_key),
                    "result_url": storage.presigned_url(app, r.result_key),
                    "download_url": storage.presigned_url(app, r.result_key, f"processed_{stem}.jpg"),
                })
        return render_template("gallery.html", items=items, user_email=g.user.email,
                               s3_enabled=storage.enabled(app),
                               bucket=app.config["S3_BUCKET"], region=app.config["AWS_REGION"])

    @app.route("/gallery/<int:image_id>/delete", methods=["POST"])
    @login_required
    def gallery_delete(image_id):
        # filter by user_id so nobody can delete someone else's image
        img = Image.query.filter_by(id=image_id, user_id=g.user.id).first_or_404()
        try:
            storage.delete_keys(app, storage.all_keys_for(img.original_key, img.result_key))
        except Exception as exc:
            app.logger.error("S3 delete failed: %s", exc)
            flash("Could not delete the files from S3. Nothing was removed; try again.", "error")
            return redirect(url_for("gallery"))
        db.session.delete(img)
        db.session.commit()
        flash("Image deleted from S3 and the database.", "success")
        return redirect(url_for("gallery"))

    return app


def _save_all(app, user_id, raw_files, results, ops):
    """
    Upload every image's 4 objects (original, result, 2 thumbnails) concurrently,
    then write all DB rows in ONE commit.
    Returns ({image_index: {"result_key": ...}}, error_message_or_None).
    """
    plans = []   # per image: dict(keys, blobs)
    def prepare(idx):
        raw, fmt, name = raw_files[idx]
        res = results[idx]
        ext, ctype = ALLOWED_FORMATS[fmt]
        uid = uuid.uuid4().hex
        keys = {
            "original": storage.make_key(user_id, uid, "original", ext),
            "result":   storage.make_key(user_id, uid, "result", "jpg"),
        }
        preview = _make_small(res["result_jpeg"], PREVIEW_MAX_SIDE)      # shown in the browser
        blobs = [
            (keys["original"], raw, ctype),
            (keys["result"], res["result_jpeg"], "image/jpeg"),
            (storage.thumb_key(keys["original"]), _make_small(raw, THUMB_MAX_SIDE, fix_orientation=True), "image/jpeg"),
            (storage.thumb_key(keys["result"]), _make_small(preview, THUMB_MAX_SIDE), "image/jpeg"),
        ]
        return {"idx": idx, "keys": keys, "blobs": blobs, "preview": preview}

    def put(job):
        idx, (key, data, ctype) = job
        try:
            storage.upload_bytes(app, key, data, ctype)
            return idx, key, None
        except Exception as exc:
            app.logger.error("S3 upload failed for %s: %s", key, exc)
            return idx, key, exc

    try:
        with ThreadPoolExecutor(max_workers=min(8, len(raw_files))) as ex:
            plans = list(ex.map(prepare, range(len(raw_files))))
        jobs = [(p["idx"], b) for p in plans for b in p["blobs"]]
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
            outcomes = list(ex.map(put, jobs))
    except Exception:
        app.logger.exception("Preparing S3 upload failed")
        return {}, "Could not prepare the images for S3. Check the server log."

    failed = {idx for idx, _, exc in outcomes if exc is not None}
    saved, rows = {}, []
    for p in plans:
        idx = p["idx"]
        if idx in failed:
            continue
        raw, fmt, name = raw_files[idx]
        res = results[idx]
        rows.append(Image(user_id=user_id, display_name=name[:255],
                          original_key=p["keys"]["original"], result_key=p["keys"]["result"],
                          operations=ops[:512], width=res["out_w"], height=res["out_h"],
                          process_ms=res["total_ms"]))
        saved[idx] = {"result_key": p["keys"]["result"], "preview": p["preview"]}

    error = None
    if rows:
        try:
            db.session.add_all(rows)
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            app.logger.error("Saving image rows failed: %s", exc)
            failed |= set(saved)          # files are in S3 but not recorded: clean them up
            saved = {}
            error = "Images were uploaded but the database (RDS) could not record them."

    if failed:
        # best-effort cleanup of orphaned objects from images that did not fully save
        orphan = [b[0] for p in plans if p["idx"] in failed for b in p["blobs"]]
        try:
            storage.delete_keys(app, orphan)
        except Exception as exc:
            app.logger.error("Orphan cleanup failed: %s", exc)
        error = error or ("Some images could not be saved to S3. Check the bucket name, "
                          "region and the instance's IAM role.")
    return saved, error


application = create_app()

if __name__ == "__main__":
    application.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
