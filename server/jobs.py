#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务队列：单 worker 串行（CPU 一次只能跑一个转写任务）。

每个任务一个 runs/<job_id>/ 目录，状态存 job.json，重启后未完成任务标记失败。
"""

import json
import threading
import time
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).parent.parent.resolve()
RUNS_DIR = APP_ROOT / "runs"

# 状态机: queued → processing → done / failed
VALID_STATUS = {"queued", "processing", "done", "failed"}


class Job:
    def __init__(self, job_id, filename, polish=False, prompt=None, domain="general"):
        self.id = job_id
        self.filename = filename
        self.polish = polish
        self.prompt = prompt
        self.domain = domain
        self.status = "queued"
        self.stage = ""
        self.progress = 0
        self.message = ""
        self.error = None
        self.created = time.time()
        self.finished = None
        self.stats = None
        self.files = {}

    def to_dict(self):
        return {
            "id": self.id, "filename": self.filename,
            "polish": self.polish, "domain": self.domain, "status": self.status,
            "stage": self.stage, "progress": self.progress,
            "message": self.message, "error": self.error,
            "created": self.created, "finished": self.finished,
            "stats": self.stats, "files": self.files,
        }

    @classmethod
    def from_dict(cls, d):
        j = cls(d["id"], d["filename"], d.get("polish", False), d.get("prompt"),
                d.get("domain", "general"))
        for k in ("status", "stage", "progress", "message", "error",
                  "created", "finished", "stats", "files"):
            setattr(j, k, d.get(k))
        return j

    @property
    def dir(self) -> Path:
        return RUNS_DIR / self.id

    @property
    def job_file(self) -> Path:
        return self.dir / "job.json"


class JobManager:
    def __init__(self):
        RUNS_DIR.mkdir(exist_ok=True)
        self._jobs = {}
        self._order = []  # 保持创建顺序
        self._lock = threading.Lock()
        self._queue = []
        self._cond = threading.Condition()
        self._worker = None
        self._load_existing()
        self._start_worker()

    # ---------- 持久化 ----------

    def _load_existing(self):
        for d in sorted(RUNS_DIR.iterdir()):
            jf = d / "job.json"
            if not d.is_dir() or not jf.exists():
                continue
            try:
                job = Job.from_dict(json.loads(jf.read_text()))
            except (json.JSONDecodeError, KeyError):
                continue
            if job.status in ("queued", "processing"):
                # 重启后进行中的任务已中断（processing 的分段进度保留可续传；queued 未开始）
                was_processing = (job.status == "processing")
                job.status = "failed"
                if was_processing:
                    job.error = "服务重启时任务被中断（分段进度已保留，重新提交同一文件可续传）"
                else:
                    job.error = "服务重启，任务尚未开始，请重新提交"
                self._save(job)
            self._jobs[job.id] = job
            self._order.append(job.id)

    def _save(self, job: Job):
        job.dir.mkdir(parents=True, exist_ok=True)
        tmp = job.job_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2))
        tmp.replace(job.job_file)

    # ---------- 对外接口 ----------

    def create(self, upload_path: Path, polish=False, prompt=None,
               display_name=None, domain="general") -> Job:
        job_id = time.strftime("%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        job = Job(job_id, display_name or upload_path.name, polish, prompt, domain)
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
        # 把上传文件移进任务目录
        job.dir.mkdir(parents=True, exist_ok=True)
        target = job.dir / ("upload" + upload_path.suffix.lower())
        shutil_move(upload_path, target)
        self._save(job)
        with self._cond:
            self._queue.append(job_id)
            self._cond.notify()
        return job

    def get(self, job_id) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self):
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order) if i in self._jobs]

    def delete(self, job_id):
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if job:
                self._order.remove(job_id)
        if job and job.dir.exists():
            import shutil
            shutil.rmtree(job.dir, ignore_errors=True)

    def download_path(self, job_id, kind) -> Path | None:
        job = self.get(job_id)
        if not job or job.status != "done":
            return None
        names = {"txt": "result.txt", "srt": "result.srt",
                 "docx": "result.docx", "json": "result.json"}
        if job.polish and kind in ("txt", "docx", "json"):
            polished = job.dir / f"result_polished{Path(names[kind]).suffix}"
            if polished.exists():
                return polished
        p = job.dir / names[kind]
        return p if p.exists() else None

    # ---------- worker ----------

    def _start_worker(self):
        self._worker = threading.Thread(target=self._loop, daemon=True,
                                        name="transcribe-worker")
        self._worker.start()

    def _loop(self):
        while True:
            with self._cond:
                while not self._queue:
                    self._cond.wait()
                job_id = self._queue.pop(0)
            job = self.get(job_id)
            if not job:
                continue
            self._run(job)

    def _run(self, job: Job):
        # 导入放 worker 里：MOSS 模型很重，避免阻塞服务启动
        from pipeline import run_pipeline

        job.status = "processing"
        job.stage = "prepare"
        self._save(job)

        def cb(stage, pct, msg):
            job.stage = stage
            job.progress = int(pct)
            job.message = msg
            if int(pct) % 5 == 0 or stage != "transcribe":  # 节流落盘
                self._save(job)

        try:
            upload = next(job.dir.glob("upload.*"))
            result, files = run_pipeline(upload, job.dir, prompt=job.prompt,
                                         domain=job.domain,
                                         progress_cb=cb)
            job.stats = result["stats"]
            job.files = files

            # 可选：LLM 深度纠错
            if job.polish:
                try:
                    job.message = "LLM 深度纠错中..."
                    self._save(job)
                    from polish import polish_result
                    polish_result(job.dir, result, domain=job.domain,
                                  cb=lambda p, m: (setattr(job, "progress", int(p)),
                                                   self._save(job)))
                    job.files = dict(files)
                    job.files["polished"] = True
                except Exception as e:
                    job.files["polish_error"] = str(e)

            job.status = "done"
            job.progress = 100
            job.finished = time.time()
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.finished = time.time()
        self._save(job)


def shutil_move(src: Path, dst: Path):
    import shutil
    shutil.move(str(src), str(dst))
