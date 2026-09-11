"""Restricted, localhost-only human adjudication of an immutable blinded queue."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import audit_protocol_v3 as v3
from .audit import CONTENT_TYPES, PRESENCE_VALUES
from .audit_finalization import load, now, require, verify_lock, write_once
from .reduced_audit_interface import ReviewerRegistry
from .safety import require_restricted_destination, sha256_file, sha256_json


def verify_queue(root: Path):
    lock = verify_lock(root)
    cert = load(root / "adjudication_queue_certificate.json")
    require(cert["annotation_lock_sha256"] == sha256_file(root / "blinded_annotation_lock.json"), "queue annotation lock differs")
    for filename, key in (("adjudication_queue_restricted.json", "queue_sha256"),
                          ("adjudication_linkage_restricted.json", "linkage_sha256")):
        require(sha256_file(root / filename) == cert[key], "adjudication queue changed")
    queue = load(root / "adjudication_queue_restricted.json")
    return lock, cert, queue


def decision_values(task, supplied):
    require(isinstance(supplied, dict) and set(supplied) == set(task["fields"]), "adjudication field set differs")
    clean = {}
    for field, value in supplied.items():
        require(isinstance(value, str) and len(value) <= 200, "invalid adjudication value")
        if field == "acquisition_content_type":
            require(value in CONTENT_TYPES, "acquisition decision required")
        elif field in {*v3.V3_CLIP_PRESENCE_FIELDS, v3.V3_SOURCE_ONLY_PRIMARY_FIELD}:
            require(value in PRESENCE_VALUES, "presence decision required")
        elif field in v3.V3_SOURCE_ONLY_CATEGORY_FIELDS:
            require(value in {"yes", "no", "uncertain", "not_assessable"}, "source category decision required")
        else:
            require(field in {*v3.V3_CLIP_FREE_TEXT_FIELDS, *v3.V3_SOURCE_ONLY_FREE_TEXT_FIELDS} - {"restricted_notes"}, "unknown adjudication field")
        clean[field] = value.strip()
    return clean


class AdjudicationStore:
    def __init__(self, root: Path, media_root: Path):
        self.root = require_restricted_destination(root)
        self.media_root = require_restricted_destination(media_root)
        lock, self.certificate, queue = verify_queue(root)
        self.tasks = {t["token"]: t for t in queue["tasks"]}
        require(len(self.tasks) == len(queue["tasks"]), "duplicate adjudication token")
        self.linkage = load(root / "adjudication_linkage_restricted.json")
        require(set(self.linkage) == set(self.tasks), "queue media linkage differs")
        self.registry = ReviewerRegistry(Path(lock["audit_root"]) / "restricted/reviewer_registry", initialize=False)
        self.records = root / "human_adjudication"
        self.records.mkdir(exist_ok=True, mode=0o700)
        self.mutex = threading.Lock()

    def progress(self):
        completed = []
        for token in self.tasks:
            path = self.records / (token + ".json")
            if not path.exists():
                continue
            d = load(path)
            require(d.get("token") == token and d.get("queue_sha256") == self.certificate["queue_sha256"], "adjudication identity differs")
            require(d.get("human_confirmed") is True and d.get("qualified_physician_attested") is True, "adjudication human confirmation absent")
            self.registry.require_active_qualified(d["adjudicator_code"])
            decision_values(self.tasks[token], d["decisions"])
            require(d.get("content_sha256") == sha256_json({k:v for k,v in d.items() if k != "content_sha256"}), "adjudication record hash differs")
            completed.append(token)
        return {"total": len(self.tasks), "completed": len(completed), "remaining": len(self.tasks)-len(completed)}

    def next_task(self):
        self.progress()
        return next((t for token, t in self.tasks.items() if not (self.records / (token + ".json")).exists()), None)

    def save(self, token, values, reviewer, confirmed):
        with self.mutex:
            require(not (self.root / "adjudication_completion_lock.json").exists(), "adjudication already locked")
            require(token in self.tasks and confirmed is True, "human confirmation required")
            self.registry.require_active_qualified(reviewer)
            clean = decision_values(self.tasks[token], values)
            record = {"schema_version": "JDIM_BLINDED_HUMAN_ADJUDICATION_V1", "token": token,
                      "queue_sha256": self.certificate["queue_sha256"], "decisions": clean,
                      "adjudicator_code": reviewer, "qualified_physician_attested": True,
                      "human_confirmed": True, "completed_at_utc": now()}
            record["content_sha256"] = sha256_json(record)
            write_once(self.records / (token + ".json"), record)
            return self.progress()

    def media_path(self, token, panel):
        require(token in self.tasks and panel in {"source", "model"}, "invalid media request")
        media_id = self.linkage[token].get("source_media_id" if panel == "source" else "model_input_media_id", "")
        require(isinstance(media_id, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", media_id)), "media unavailable")
        path = self.media_root / (media_id + ".png")
        require(path.is_file() and path.resolve().parent == self.media_root.resolve(), "media root differs")
        return path

    def technical_check(self):
        checked = 0
        for token, task in self.tasks.items():
            panels = ("source", "model") if task["evidence_tier"] == "EXACT_MODEL_INPUT" else ("source",)
            for panel in panels:
                with self.media_path(token, panel).open("rb") as stream:
                    require(stream.read(8) == b"\x89PNG\r\n\x1a\n", "protected media not readable PNG")
                checked += 1
        return {"queue_items": len(self.tasks), "protected_media_headers_verified": checked,
                "pixels_interpreted": False, "target_values_read": False}


def lock_adjudication(root: Path):
    _, cert, queue = verify_queue(root)
    records = root / "human_adjudication"
    hashes = {}
    for task in queue["tasks"]:
        path = records / (task["token"] + ".json")
        require(path.is_file(), "human adjudication remains incomplete")
        d = load(path)
        require(d.get("token") == task["token"] and d.get("queue_sha256") == cert["queue_sha256"], "adjudication identity differs")
        require(d.get("human_confirmed") is True and d.get("qualified_physician_attested") is True, "adjudication confirmation missing")
        decision_values(task, d["decisions"])
        require(d["content_sha256"] == sha256_json({k:v for k,v in d.items() if k != "content_sha256"}), "adjudication content changed")
        hashes[path.name] = sha256_file(path)
    payload = {"status": "BLINDED_ADJUDICATION_LOCKED", "created_at_utc": now(),
               "queue_sha256": cert["queue_sha256"], "completed_items": len(hashes),
               "record_hashes": hashes, "human_decisions_only": True, "target_values_read": False}
    digest = write_once(root / "adjudication_completion_lock.json", payload)
    return {"status": payload["status"], "completed_items": len(hashes), "certificate_sha256": digest}


HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Blinded Audit Adjudication</title>
<style>body{margin:0;font:15px Arial,sans-serif;color:#17251e;background:#fff}header{padding:14px 24px;background:#ebf2ee;border-bottom:1px solid #9eafa4}main{max-width:1400px;margin:auto;padding:20px}h1{font-size:21px;margin:0}h2{font-size:18px}button,input,select{font:inherit;padding:9px;border:1px solid #74857b;border-radius:4px}button{cursor:pointer;background:#ebf2ee}button:disabled{opacity:.5}label{display:block;margin:12px 0}#scope{padding:12px;border-left:4px solid #25664a;background:#edf4ef;line-height:1.5}.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px}canvas{width:100%;aspect-ratio:1;display:block;background:#111;max-height:620px;object-fit:contain}.source-only .panels{grid-template-columns:1fr}.source-only #model-panel{display:none}table{width:100%;border-collapse:collapse;margin:20px 0}td,th{text-align:left;border-bottom:1px solid #aaa;padding:10px;overflow-wrap:anywhere}td input,td select{max-width:100%;box-sizing:border-box}#message{color:#9d2626;white-space:pre-wrap}nav{display:flex;gap:12px;align-items:center;margin:14px 0}#reader{max-width:400px}.hidden{display:none}@media(max-width:700px){.panels{grid-template-columns:1fr}main{padding:12px}td,th{padding:5px;font-size:13px}td input{width:100%}}</style>
<header><h1>Blinded Audit Adjudication</h1></header><main><section id="login"><label>Registered reviewer code<input id="reader" autocomplete="off"></label><label><input type="checkbox" id="qualified"> I am a qualified physician performing blinded adjudication.</label><button id="enter">Start adjudication</button></section>
<section id="review" hidden><h2 id="progress"></h2><p id="scope"></p><div class="panels"><section><h2>Source acquisition</h2><canvas id="source" width="640" height="640"></canvas></section><section id="model-panel"><h2>Exact model input</h2><canvas id="model" width="640" height="640"></canvas></section></div><nav><button id="prev" title="Previous frame">&larr;</button><span id="frame"></span><button id="next" title="Next frame">&rarr;</button><button id="play">Play</button></nav><form id="decision-form"><table><thead><tr><th>Field</th><th>Primary</th><th>Secondary</th><th>Adjudicated response</th></tr></thead><tbody id="fields"></tbody></table><label><input type="checkbox" id="confirm"> I confirm these decisions after reviewing the scored panel.</label><button type="submit">Lock decisions and continue</button></form></section><p id="message" role="status"></p></main>
<script>let task=null,csrf='',frame=0,timer=null;const images={source:null,model:null};const message=document.querySelector('#message');async function api(url,payload){const r=await fetch(url,{method:payload?'POST':'GET',headers:payload?{'Content-Type':'application/json','X-CSRF':csrf}:{},body:payload?JSON.stringify(payload):undefined});const d=await r.json();if(!r.ok)throw new Error(d.message||'Request failed');return d}function draw(){for(const panel of ['source','model']){const c=document.querySelector('#'+panel),ctx=c.getContext('2d'),img=images[panel];ctx.fillStyle='#111';ctx.fillRect(0,0,640,640);if(img){const w=img.width/4,h=img.height/4;ctx.drawImage(img,(frame%4)*w,Math.floor(frame/4)*h,w,h,0,0,640,640)}}document.querySelector('#frame').textContent=`Frame ${frame+1} / 16`}function stop(){clearInterval(timer);timer=null;document.querySelector('#play').textContent='Play'}async function next(){stop();message.textContent='';const d=await api('/api/next');task=d.task;document.querySelector('#progress').textContent=`Completed ${d.progress.completed} / ${d.progress.total}`;if(!task){document.querySelector('#review').hidden=true;message.textContent='All adjudication items completed. The owner can verify and lock the set.';return}document.querySelector('#review').hidden=false;document.querySelector('#confirm').checked=false;document.body.classList.toggle('source-only',task.evidence_tier!=='EXACT_MODEL_INPUT');document.querySelector('#scope').textContent=d.scope;const body=document.querySelector('#fields');body.replaceChildren();for(const f of task.fields){const row=document.createElement('tr');for(const text of [f.replaceAll('_',' '),task.primary[f]||'',task.secondary?.[f]||'']){const td=document.createElement('td');td.textContent=text;row.append(td)}const td=document.createElement('td');let input;if(d.options[f]){input=document.createElement('select');const blank=document.createElement('option');blank.value='';blank.textContent='Select';input.append(blank);for(const v of d.options[f]){const o=document.createElement('option');o.value=v;o.textContent=v.replaceAll('_',' ');input.append(o)}input.required=true}else{input=document.createElement('input');input.maxLength=200}input.name=f;td.append(input);row.append(td);body.append(row)}frame=0;images.source=null;images.model=null;draw();for(const panel of ['source','model']){if(panel==='model'&&task.evidence_tier!=='EXACT_MODEL_INPUT')continue;const image=new Image();image.onload=()=>{images[panel]=image;draw()};image.onerror=()=>message.textContent='Protected media could not load. Do not finalize this item.';image.src=`/media/${task.token}/${panel}`}}
document.querySelector('#enter').onclick=async()=>{try{const d=await api('/api/login',{reviewer:document.querySelector('#reader').value.trim(),qualified:document.querySelector('#qualified').checked});csrf=d.csrf;document.querySelector('#login').hidden=true;await next()}catch(e){message.textContent=e.message}};document.querySelector('#prev').onclick=()=>{stop();frame=(frame+15)%16;draw()};document.querySelector('#next').onclick=()=>{stop();frame=(frame+1)%16;draw()};document.querySelector('#play').onclick=()=>{if(timer){stop()}else{timer=setInterval(()=>{frame=(frame+1)%16;draw()},200);document.querySelector('#play').textContent='Pause'}};document.querySelector('#decision-form').onsubmit=async e=>{e.preventDefault();if(!images.source||(task.evidence_tier==='EXACT_MODEL_INPUT'&&!images.model)){message.textContent='Both required panels must load before confirmation.';return}try{await api('/api/save',{token:task.token,decisions:Object.fromEntries(new FormData(e.target)),confirmed:document.querySelector('#confirm').checked});await next()}catch(err){message.textContent=err.message}};</script></html>'''


def serve(root: Path, media_root: Path, port: int):
    store = AdjudicationStore(root, media_root)
    technical = store.technical_check()
    sessions = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, payload, kind="application/json", cookie=None):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(raw)

        def session(self):
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            key = cookie["jdim_adjudication"].value if "jdim_adjudication" in cookie else ""
            require(key in sessions, "authenticated adjudication session required")
            return sessions[key]

        def host_ok(self):
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_GET(self):
            try:
                require(self.host_ok(), "localhost host required")
                if self.path == "/":
                    return self.reply(200, HTML.encode(), "text/html; charset=utf-8")
                if self.path == "/health":
                    return self.reply(200, {"status":"READY_FOR_BLINDED_ADJUDICATION", **store.progress()})
                self.session()
                if self.path == "/api/next":
                    task = store.next_task()
                    options = {}
                    if task:
                        for f in task["fields"]:
                            if f == "acquisition_content_type": options[f] = sorted(CONTENT_TYPES)
                            elif f not in {*v3.V3_CLIP_FREE_TEXT_FIELDS, *v3.V3_SOURCE_ONLY_FREE_TEXT_FIELDS}: options[f] = sorted(PRESENCE_VALUES)
                    return self.reply(200, {"task":task, "progress":store.progress(), "options":options,
                        "scope":v3.SCORING_SCOPE_BY_TIER[task["evidence_tier"]] if task else ""})
                match = re.fullmatch(r"/media/(ADJ-[A-F0-9]{24})/(source|model)", self.path)
                require(match is not None, "route unavailable")
                return self.reply(200, store.media_path(*match.groups()).read_bytes(), "image/png")
            except Exception:
                self.reply(403, {"message":"Access or integrity validation failed. No record was changed."})

        def do_POST(self):
            try:
                require(self.host_ok(), "localhost host required")
                origin = self.headers.get("Origin")
                require(origin in {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}, "same-origin request required")
                length = int(self.headers.get("Content-Length", "0"))
                require(0 < length <= 20000, "request size invalid")
                data = json.loads(self.rfile.read(length))
                if self.path == "/api/login":
                    require(set(data) == {"reviewer","qualified"} and data["qualified"] is True, "qualified physician confirmation required")
                    store.registry.require_active_qualified(data["reviewer"])
                    key, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    sessions[key] = {"reviewer":data["reviewer"], "csrf":csrf}
                    return self.reply(200, {"csrf":csrf}, cookie=f"jdim_adjudication={key}; HttpOnly; SameSite=Strict; Path=/")
                session = self.session()
                require(secrets.compare_digest(self.headers.get("X-CSRF", ""), session["csrf"]), "CSRF confirmation required")
                require(self.path == "/api/save" and set(data) == {"token","decisions","confirmed"}, "route or fields unavailable")
                self.reply(200, store.save(data["token"],data["decisions"],session["reviewer"],data["confirmed"]))
            except Exception:
                self.reply(403, {"message":"Request not accepted. Existing locked records were preserved."})

    server = ThreadingHTTPServer(("127.0.0.1",port),Handler)
    print(json.dumps({"status":"READY_FOR_BLINDED_ADJUDICATION", **technical, **store.progress(), "localhost_only":True}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
