"""
PyHost Cloud — Telegram Bot Hosting Bot  (single-file edition)
================================================================
ইউজাররা নিজের Python বট/স্ক্রিপ্ট এই বটে আপলোড করে হোস্ট করতে পারে।

ফাইল মাত্র ৩টি:  app.py  •  .env  •  requirements.txt

সেটআপ (Ubuntu/Debian, root হিসেবে — ইউজার আইসোলেশনের জন্য):
    apt install -y python3 python3-venv python3-pip git unzip
    mkdir -p /opt/pyhost && cd /opt/pyhost          # তিনটা ফাইল এখানে রাখুন
    python3 -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt
    chmod -R a+rX /opt/venv /opt/pyhost && chmod 600 .env
    nano .env                                       # BOT_TOKEN, ADMIN_IDS দিন
    /opt/venv/bin/python app.py
venv অবশ্যই /opt এর মতো সবার-পড়ার-যোগ্য জায়গায় রাখুন (/root এর ভেতরে নয়)।

নোট:
  • চ্যানেল ভেরিফিকেশন: বটকে চ্যানেলে Admin বানাতে হবে। চ্যানেল/bKash/Nagad/রেফার রিওয়ার্ড সব
    Admin Panel → সেটিংস থেকে বদলানো যায়।
  • পেমেন্ট ম্যানুয়াল: ইউজার TrxID দেয় → অ্যাডমিনের কাছে Approve/Reject বাটন যায়।
  • root হিসেবে চালালে (ISOLATE_USERS=1) প্রতিটি ইউজারের কোড আলাদা Linux uid-এ চলে। root ছাড়া চালালে
    এই সুরক্ষা বন্ধ থাকে — তখন ইউজার বটের .env/ডাটাবেস পড়তে পারবে।
  • ফাইল-অপারেশন সাব-প্রসেস এই ফাইলই চালায়:  python app.py --fsworker <op> ...
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile


# ==============================================================================
# 1. SANDBOXED FILE WORKER  (runs as the project owner's OS user — keep it stdlib-only)
# ==============================================================================
_FS_RAW = False


def _fs_out(**kw):
    sys.stdout.write(json.dumps(kw))
    sys.stdout.flush()
    sys.exit(0)


def _fs_fail(msg):
    if _FS_RAW:
        sys.stderr.write(msg)
        sys.exit(2)
    _fs_out(ok=False, error=msg)


def _fs_jail(base, rel):
    b = os.path.realpath(base)
    t = os.path.realpath(os.path.join(b, rel.strip("/")))
    if t != b and not t.startswith(b + os.sep):
        _fs_fail("পাথ অনুমোদিত নয়")
    return b, t


def _fs_op_ls(base, rel):
    _, t = _fs_jail(base, rel)
    if not os.path.isdir(t):
        _fs_fail("ফোল্ডার পাওয়া যায়নি")
    items = []
    for e in os.scandir(t):
        try:
            d = e.is_dir(follow_symlinks=False)
            s = 0 if d else e.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        items.append({"n": e.name, "d": d, "s": s})
    items.sort(key=lambda i: (not i["d"], i["n"].lower()))
    _fs_out(ok=True, items=items)


def _fs_op_exists(base, rel):
    _, t = _fs_jail(base, rel)
    if not os.path.isfile(t):
        _fs_fail(f"ফাইল পাওয়া যায়নি: {rel}")
    _fs_out(ok=True)


def _fs_op_read(base, rel, maxb):
    _, t = _fs_jail(base, rel)
    if not os.path.isfile(t):
        _fs_fail("ফাইল পাওয়া যায়নি")
    with open(t, "rb") as f:
        sys.stdout.buffer.write(f.read(int(maxb) + 1))
    sys.exit(0)


def _fs_op_write(base, rel, maxb):
    _, t = _fs_jail(base, rel)
    data = sys.stdin.buffer.read()
    if len(data) > int(maxb):
        _fs_fail("ফাইল অনেক বড়")
    os.makedirs(os.path.dirname(t), exist_ok=True)
    with open(t, "wb") as f:
        f.write(data)
    _fs_out(ok=True, size=len(data))


def _fs_op_mkdir(base, rel):
    _, t = _fs_jail(base, rel)
    os.makedirs(t, exist_ok=True)
    _fs_out(ok=True)


def _fs_op_rm(base, rel):
    if not rel.strip("/"):
        _fs_fail("রুট ফোল্ডার ডিলিট করা যাবে না")
    _, parent_real = _fs_jail(base, os.path.dirname(rel.strip("/")))
    t = os.path.join(parent_real, os.path.basename(rel.strip("/")))
    if os.path.islink(t) or os.path.isfile(t):
        os.unlink(t)
    elif os.path.isdir(t):
        shutil.rmtree(t)
    else:
        _fs_fail("পাওয়া যায়নি")
    _fs_out(ok=True)


def _fs_op_mv(base, rel, rel2):
    _, a = _fs_jail(base, rel)
    _, b = _fs_jail(base, rel2)
    if not os.path.exists(a):
        _fs_fail("পাওয়া যায়নি")
    os.makedirs(os.path.dirname(b), exist_ok=True)
    os.rename(a, b)
    _fs_out(ok=True)


def _fs_op_du(base, rel):
    total = 0
    for root, _dirs, files in os.walk(base):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    _fs_out(ok=True, size=total)


def _fs_op_find_py(base, rel):
    found = []
    base = os.path.realpath(base)
    for root, dirs, files in os.walk(base):
        depth = os.path.relpath(root, base).count(os.sep)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", "site-packages")
                   and depth < 2]
        for f in files:
            if f.endswith(".py"):
                found.append(os.path.relpath(os.path.join(root, f), base))
    found.sort(key=lambda p: (p.count(os.sep), p))
    _fs_out(ok=True, files=found[:40])


def _fs_extract(zf, dest, maxb):
    infos = [i for i in zf.infolist()]
    if sum(i.file_size for i in infos) > int(maxb):
        _fs_fail("আনজিপ সাইজ লিমিট ছাড়িয়ে গেছে")
    names = [i.filename for i in infos if not i.is_dir()]
    if not names:
        _fs_fail("জিপ ফাইল খালি")
    tops = {n.split("/")[0] for n in names}
    strip = len(tops) == 1 and all("/" in n for n in names)  # GitHub-style single root folder
    dest = os.path.realpath(dest)
    os.makedirs(dest, exist_ok=True)
    count = 0
    for i in infos:
        n = i.filename
        if strip:
            n = n.split("/", 1)[1] if "/" in n else ""
        if not n or i.is_dir():
            continue
        if (i.external_attr >> 16) & 0o170000 == 0o120000:  # symlink entry
            continue
        t = os.path.normpath(os.path.join(dest, n))
        if not t.startswith(dest + os.sep):
            continue
        os.makedirs(os.path.dirname(t), exist_ok=True)
        with zf.open(i) as s, open(t, "wb") as d:
            shutil.copyfileobj(s, d)
        count += 1
    _fs_out(ok=True, count=count)


def _fs_op_unzip(base, rel, maxb):
    """zip bytes come from stdin"""
    _, t = _fs_jail(base, rel)
    with tempfile.TemporaryFile() as tmp:
        shutil.copyfileobj(sys.stdin.buffer, tmp)
        tmp.seek(0)
        try:
            zf = zipfile.ZipFile(tmp)
        except zipfile.BadZipFile:
            _fs_fail("এটা বৈধ জিপ ফাইল নয়")
        _fs_extract(zf, t, maxb)


def _fs_op_unzipfile(base, rel, maxb, reldest):
    _, z = _fs_jail(base, rel)
    _, d = _fs_jail(base, reldest)
    try:
        zf = zipfile.ZipFile(z)
    except Exception:
        _fs_fail("এটা বৈধ জিপ ফাইল নয়")
    _fs_extract(zf, d, maxb)


def _fs_op_zip(base, rel, fd):
    skip = {"__pycache__", ".git", "node_modules"}
    base = os.path.realpath(base)
    with zipfile.ZipFile(os.fdopen(int(fd), "w+b"), "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip]
            for f in files:
                p = os.path.join(root, f)
                if os.path.islink(p):
                    continue
                z.write(p, os.path.relpath(p, base))
    _fs_out(ok=True)


_DATA_EXT = (".db", ".sqlite", ".sqlite3", ".json", ".csv", ".pkl", ".pickle", ".dat", ".data")
_DATA_SKIP_NAMES = {"package.json", "package-lock.json", "tsconfig.json", "composer.json"}
_DATA_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".pylibs", "venv", ".venv"}


def _fs_is_data(rel):
    """A project 'data file': anything inside data/ or a root-level .db/.sqlite/.json/.csv/.pkl ... file."""
    parts = rel.replace("\\", "/").lstrip("/").split("/")
    if any(x in _DATA_SKIP_DIRS or x in ("", ".", "..") for x in parts):
        return False
    if parts[0] == "data" and len(parts) > 1:
        return True
    return len(parts) == 1 and parts[0].lower().endswith(_DATA_EXT) and parts[0] not in _DATA_SKIP_NAMES


def _fs_op_zipdata(base, rel, fd, maxb):
    base = os.path.realpath(base)
    picked, total = [], 0
    for root, dirs, files in os.walk(base):
        if root == base:
            dirs[:] = [d for d in dirs if d == "data"]
        else:
            dirs[:] = sorted(d for d in dirs if d not in _DATA_SKIP_DIRS)
        for f in sorted(files):
            p = os.path.join(root, f)
            r = os.path.relpath(p, base)
            if os.path.islink(p) or not _fs_is_data(r):
                continue
            picked.append((p, r))
            total += os.path.getsize(p)
    if total > int(maxb):
        _fs_fail("ডাটা সাইজ লিমিট ছাড়িয়ে গেছে")
    with zipfile.ZipFile(os.fdopen(int(fd), "w+b"), "w", zipfile.ZIP_DEFLATED) as z:
        for p, r in picked:
            z.write(p, r)
    _fs_out(ok=True, count=len(picked), size=total)


def _fs_op_restoredata(base, rel, mode, maxb):
    """zip bytes come from stdin. mode 'missing' = only create absent files, 'all' = overwrite."""
    base = os.path.realpath(base)
    with tempfile.TemporaryFile() as tmp:
        shutil.copyfileobj(sys.stdin.buffer, tmp)
        tmp.seek(0)
        try:
            zf = zipfile.ZipFile(tmp)
        except zipfile.BadZipFile:
            _fs_fail("এটা বৈধ জিপ ফাইল নয়")
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if sum(i.file_size for i in infos) > int(maxb):
            _fs_fail("আনজিপ সাইজ লিমিট ছাড়িয়ে গেছে")
        tops = {i.filename.split("/")[0] for i in infos}
        strip = len(tops) == 1 and all("/" in i.filename for i in infos) and "data" not in tops
        count = 0
        for i in infos:
            name = i.filename.replace("\\", "/").lstrip("/")
            if strip:
                name = name.split("/", 1)[1]
            if not _fs_is_data(name) or (i.external_attr >> 16) & 0o170000 == 0o120000:
                continue
            t = os.path.normpath(os.path.join(base, name))
            if not t.startswith(base + os.sep):
                continue
            if mode == "missing" and os.path.exists(t):
                continue
            os.makedirs(os.path.dirname(t), exist_ok=True)
            with zf.open(i) as s, open(t, "wb") as d:
                shutil.copyfileobj(s, d)
            count += 1
    _fs_out(ok=True, count=count)


_FS_OPS = {"zipdata": _fs_op_zipdata, "restoredata": _fs_op_restoredata, "ls": _fs_op_ls, "exists": _fs_op_exists, "read": _fs_op_read, "write": _fs_op_write,
           "mkdir": _fs_op_mkdir, "rm": _fs_op_rm, "mv": _fs_op_mv, "du": _fs_op_du, "find_py": _fs_op_find_py,
           "unzip": _fs_op_unzip, "unzipfile": _fs_op_unzipfile, "zip": _fs_op_zip}


def _fs_main(argv):
    global _FS_RAW
    op = argv[0]
    _FS_RAW = op == "read"
    try:
        _FS_OPS[op](*argv[1:])
    except SystemExit:
        raise
    except Exception as e:  # noqa
        _fs_fail(f"{type(e).__name__}: {e}")


# Sub-process mode:  python app.py --fsworker <op> <base> <rel> [extra...]
# Handled right here — before .env / database / telegram are touched — because the bot launches this
# as the project owner's unprivileged OS user, which must never read the bot's secrets.
if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--fsworker":
    _fs_main(sys.argv[2:])
    sys.exit(0)

# ---- everything below needs more than the standard file-ops (only loaded in normal bot mode) ----
import functools
import hashlib
import html
import logging
import posixpath
import re
import secrets
import urllib.parse
import urllib.request
import signal
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException


# ==============================================================================
# 2. CONFIG  (values come from .env)
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent


def _load_env():
    # ".env" (or a file simply named "env") next to app.py
    p = next((BASE_DIR / n for n in (".env", "env") if (BASE_DIR / n).is_file()), None)
    if p:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

BOT_TOKEN = os.getenv("BOT_TOKEN", "9002134177:AAGGX4TdlhhDfzNWQu2oZPJTYnN_9LU4JHc")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "9216949689").replace(" ", "").split(",") if x.isdigit()}
TZ = os.getenv("TZ_NAME", "Asia/Dhaka")

# VPLink verification (free plan after the user passes the VPLink short link). Put these in .env:
#   VPLINK_API_KEY=...                      (from your VPLink dashboard -> API)
#   VPLINK_API_URL=https://vplink.in/api    (use the endpoint shown in your dashboard)
VPLINK_API_KEY = os.getenv("VPLINK_API_KEY", "").strip()
VPLINK_API_URL = os.getenv("VPLINK_API_URL", "https://vplink.in/api").strip()
VPLINK_MIN_SECONDS = int(os.getenv("VPLINK_MIN_SECONDS", "12"))   # coming back faster than this = link was skipped
VPLINK_TOKEN_TTL = 30 * 60                                        # a verify link is valid for 30 minutes

# Per-user OS isolation (needs the bot to run as root on Linux). Strongly recommended.
ISOLATE = os.getenv("ISOLATE_USERS", "1") == "1"
ISO_BASE_UID = int(os.getenv("ISO_BASE_UID", "100000"))

DATA_DIR = BASE_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
LOGS_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "host.db"
for _d in (DATA_DIR, PROJECTS_DIR, LOGS_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = 20          # Telegram Bot API download limit is 20 MB
MAX_UNZIP_MB = 300
MAX_FILE_MB = 500           # RLIMIT_FSIZE for user processes
MONITOR_INTERVAL = 4        # seconds
MAX_AUTO_RESTARTS = 5       # per 10 minutes
DATA_BK_MAX_MB = int(os.getenv("DATA_BK_MAX_MB", "100"))     # max size of one project's data snapshot
DATA_BK_MINUTES = int(os.getenv("DATA_BK_MINUTES", "30"))   # auto data-backup interval (running projects)
DATA_BK_KEEP = 3                                            # data snapshots kept per project
CPU_STRIKES = 10            # consecutive monitor ticks above the plan CPU limit before kill

# Defaults — ALL of these are editable later from the Admin Panel (Settings / Welcome message)
_WELCOME_V1 = """এটা একটা Python বট হোস্টিং সার্ভিস — পুরোটাই Telegram থেকে চালানো যায়। আপনার বট/স্ক্রিপ্টের ফাইল আপলোড করুন, ▶️ চালু করুন — ব্যস! কোনো সার্ভার বা টার্মিনাল জানার দরকার নেই।

✨ কেন আমাদের থেকে নেবেন?

✅ ঝামেলাহীন — .py বা পুরো .zip আপলোড করুন, requirements.txt অটো ইন্সটল হবে
✅ ২৪/৭ অনলাইন — ক্র্যাশ করলে সাথে সাথে লগ ও সমাধান জানানো হয়, পেইড প্ল্যানে অটো-রিস্টার্ট
✅ সব এক জায়গায় — ফাইল ম্যানেজার, লাইভ লগ, Package Manager, গিটহাব সিঙ্ক, ব্যাকআপ
✅ নিরাপদ — প্রতিটি প্রজেক্ট আলাদা ফোল্ডারে ও আলাদা প্রসেসে চলে
✅ সাশ্রয়ী — ফ্রি ট্রায়াল, কম দামের প্ল্যান, bKash/Nagad-এ সহজ পেমেন্ট
✅ রেফার করে ফ্রিতে ব্যালেন্স আর্ন করুন"""

DEFAULT_WELCOME = """এটা একটা Python বট হোস্টিং সার্ভিস — পুরোটাই Telegram থেকে চালানো যায়। আপনার বট/স্ক্রিপ্টের ফাইল আপলোড করুন, ▶️ চালু করুন — ব্যস! কোনো সার্ভার বা টার্মিনাল জানার দরকার নেই।

✨ কেন আমাদের থেকে নেবেন?

⚡ ঝামেলাহীন — .py বা পুরো .zip আপলোড করুন, requirements.txt অটো ইন্সটল হবে
🌐 ২৪/৭ অনলাইন — ক্র্যাশ করলে সাথে সাথে লগ ও সমাধান জানানো হয়, পেইড প্ল্যানে অটো-রিস্টার্ট
🧰 সব এক জায়গায় — ফাইল ম্যানেজার, লাইভ লগ, Package Manager, গিটহাব সিঙ্ক, ব্যাকআপ
🔒 নিরাপদ — প্রতিটি প্রজেক্ট আলাদা ফোল্ডারে ও আলাদা প্রসেসে চলে
💸 সাশ্রয়ী — ফ্রি ট্রায়াল, কম দামের প্ল্যান, bKash/Nagad-এ সহজ পেমেন্ট
🎁 রেফার করে ফ্রিতে ব্যালেন্স আর্ন করুন"""

DEFAULT_SETTINGS = {
    "welcome_title": "🇫 🇷 🇪 🇪𓊈𝑽꯭𝑰꯭𝑷ࠡࠡࠡࠡࠢ𓊉 🇭 🇴 🇸 🇹 🇮 🇳 🇬",
    "welcome_text": DEFAULT_WELCOME,
    "welcome_video": "",          # "video:<file_id|url>" or "animation:<file_id>"
    "channels": "@A9XGMR_WORLD_ALL_UPDATE,@jahan_gamer_world, @JG_WORLD1, @JG_World_SUPPORT_CHAT",
    "bkash": "01763700540",
    "nagad": "01*********",
    "bkash_on": "1",          # "0" = bKash payment hidden
    "nagad_on": "1",          # "0" = Nagad payment hidden
    "payment_note": "",       # extra line shown on the payment screen
    "referral_reward": "2",
    "support": "@JAHAN_A9XGMR_WORLD",
    "admins": "",             # extra admin IDs (comma separated) — managed from Admin Panel
    "vplink_on": "0",         # "1" = users can get the plan below by verifying through VPLink
    "vplink_plan": "",        # plan id given after VPLink verification (choose from Admin Panel -> Settings)
    "vplink_days": "1",       # how many days that plan lasts
}

# id, name, price, days, max_projects, ram_mb, cpu_pct, disk_mb, auto_restart, active, sort
SEED_PLANS = [
    ("free", "🆓 FREE", 0, 7, 1, 256, 50, 100, 0, 1, 0),
    ("basic", "🥉 BASIC", 49, 30, 3, 512, 80, 500, 1, 1, 1),
    ("pro", "🥈 PRO", 99, 30, 10, 1024, 150, 2000, 1, 1, 2),
    ("premium", "👑 PREMIUM", 199, 30, 50, 2048, 200, 5000, 1, 1, 3),
    ("student", "🎓 STUDENT", 29, 30, 2, 256, 50, 300, 1, 1, 0),   # cheap plan — editable from Admin Panel
]


# ==============================================================================
# 3. UTILS
# ==============================================================================
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(TZ)
except Exception:  # tzdata missing
    _TZ = None


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def fmt_dur(sec) -> str:
    sec = int(max(0, sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def fmt_ts(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=_TZ).strftime("%d %b %Y, %I:%M %p")


def human_size(n) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def days_left(ts) -> int:
    return max(0, int((ts - time.time()) // 86400))


# ==============================================================================
# 4. DATABASE  (SQLite, thread-safe)
# ==============================================================================
# ------------------------------------------------------------------------------
# GitHub data backup / restore (keeps bot data safe across Railway redeploys & account changes)
# .env:  DATA_REPO=owner/repo   DATA_REPO_TOKEN=ghp_...   (a PRIVATE repo used ONLY for data)
#        DATA_REPO_BRANCH=main  DATA_REPO_MINUTES=30  DATA_REPO_MAX_MB=500
# ------------------------------------------------------------------------------
GH_REPO = os.getenv("DATA_REPO", "").strip()
GH_TOKEN = os.getenv("DATA_REPO_TOKEN", "").strip()
GH_BRANCH = os.getenv("DATA_REPO_BRANCH", "main").strip() or "main"
try:
    GH_MINUTES = max(5, int(os.getenv("DATA_REPO_MINUTES", "30")))
    GH_MAX_MB = max(10, int(os.getenv("DATA_REPO_MAX_MB", "500")))
except ValueError:
    GH_MINUTES, GH_MAX_MB = 30, 500
GH_PART_MB = 45                                    # GitHub rejects files > 100 MB, so the ZIP is split
GH_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".pylibs", "venv", ".venv"}
_gh_lock = threading.Lock()
_gh_block_push = False        # True = restore failed -> NEVER overwrite the GitHub backup with empty data
_gh_restore_info = {"state": "none", "msg": ""}    # none | restored | empty_repo | failed
_gh_status = {"time": None, "ok": None, "msg": "এখনো ব্যাকআপ হয়নি"}
_gh_last_sig = None
_gh_repo_checked = False


def _gh_enabled():
    return bool(GH_REPO and GH_TOKEN)


def _gh_slug():
    r = re.sub(r"^https?://github\.com/", "", GH_REPO.strip()).strip("/")
    return r[:-4] if r.endswith(".git") else r


def _gh_url():
    return f"https://x-access-token:{urllib.parse.quote(GH_TOKEN, safe='')}@github.com/{_gh_slug()}.git"


def _gh_redact(s):
    s = str(s)
    return s.replace(GH_TOKEN, "***").replace(urllib.parse.quote(GH_TOKEN, safe=""), "***") if GH_TOKEN else s


def _gh_git(args, cwd, timeout=600):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    return r.returncode, _gh_redact((r.stdout or "") + (r.stderr or "")).strip()


def _gh_restore_if_empty():
    """Runs BEFORE the database opens. If this host has no data yet, pull the last backup from GitHub."""
    global _gh_block_push
    if not _gh_enabled():
        return
    if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        return
    print("[github-backup] local data is empty -> trying to restore from GitHub", flush=True)
    if not shutil.which("git"):
        _gh_block_push = True
        _gh_restore_info.update(state="failed", msg="git ইন্সটল নেই")
        return
    tmp = Path(tempfile.mkdtemp(prefix="ghrestore_"))
    try:
        rc, out = _gh_git(["clone", "--depth", "1", "--branch", GH_BRANCH, _gh_url(), str(tmp / "repo")], tmp, 600)
        if rc != 0:
            if "Remote branch" in out and "not found" in out:      # repo exists but nothing was ever pushed
                _gh_restore_info.update(state="empty_repo", msg="রিপোতে এখনো কোনো ব্যাকআপ নেই")
                print("[github-backup] repo has no backup yet -> starting fresh", flush=True)
                return
            raise RuntimeError(out[-300:] or "git clone failed")
        repo = tmp / "repo"
        mf = json.loads((repo / "manifest.json").read_text(encoding="utf-8"))
        parts = sorted(repo.glob("backup.zip.part*"))
        if not parts:
            raise RuntimeError("backup parts পাওয়া যায়নি")
        whole = tmp / "backup.zip"
        h = hashlib.sha256()
        with open(whole, "wb") as dst:
            for part in parts:
                with open(part, "rb") as src:
                    while True:
                        b = src.read(1024 * 1024)
                        if not b:
                            break
                        h.update(b)
                        dst.write(b)
        if mf.get("sha256") and h.hexdigest() != mf["sha256"]:
            raise RuntimeError("ব্যাকআপ ফাইল নষ্ট (checksum মেলেনি)")
        ex = tmp / "extract"
        with zipfile.ZipFile(whole) as zf:
            zf.extractall(ex)
        dbf = ex / "host.db"
        if not dbf.is_file():
            raise RuntimeError("ব্যাকআপে host.db নেই")
        chk = sqlite3.connect(str(dbf))
        try:
            if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("ব্যাকআপের ডাটাবেস নষ্ট")
            chk.execute("UPDATE projects SET req_hash=NULL, pid=NULL")      # .pylibs are not backed up -> reinstall
            chk.commit()
            users = chk.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            chk.close()
        proj_src = ex / "projects"
        if proj_src.is_dir():
            shutil.copytree(proj_src, PROJECTS_DIR, dirs_exist_ok=True)
        shutil.move(str(dbf), str(DB_PATH))
        if hasattr(os, "geteuid") and os.geteuid() == 0 and ISOLATE:        # give every project back to its own uid
            for udir in PROJECTS_DIR.iterdir():
                if udir.name.isdigit():
                    iuid = ISO_BASE_UID + (int(udir.name) % 2_000_000_000)
                    for root, dirs, files in os.walk(udir):
                        for n in dirs + files:
                            try:
                                os.chown(os.path.join(root, n), iuid, iuid)
                            except OSError:
                                pass
                    try:
                        os.chown(udir, iuid, iuid)
                    except OSError:
                        pass
        _gh_restore_info.update(state="restored", msg=f"{users} জন ইউজারের ডাটা ফিরে এসেছে")
        print(f"[github-backup] RESTORED ({users} users)", flush=True)
    except Exception as e:  # noqa
        _gh_block_push = True
        if DB_PATH.exists() and DB_PATH.stat().st_size == 0:
            DB_PATH.unlink()
        _gh_restore_info.update(state="failed", msg=_gh_redact(f"{type(e).__name__}: {e}")[:300])
        print("[github-backup] RESTORE FAILED:", _gh_restore_info["msg"], flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_gh_restore_if_empty()

_db_lock = threading.RLock()
_db_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
_db_conn.row_factory = sqlite3.Row
_db_conn.execute("PRAGMA journal_mode=WAL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at REAL,
  balance REAL DEFAULT 0, referred_by INTEGER, ref_count INTEGER DEFAULT 0, ref_rewarded INTEGER DEFAULT 0,
  verified INTEGER DEFAULT 0, banned INTEGER DEFAULT 0,
  plan_id TEXT DEFAULT 'free', plan_expires REAL, trial_started REAL,
  exp_notified REAL DEFAULT 0, trial_notified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS plans(
  id TEXT PRIMARY KEY, name TEXT, price REAL, days INTEGER, max_projects INTEGER, ram_mb INTEGER,
  cpu_pct INTEGER, disk_mb INTEGER, auto_restart INTEGER DEFAULT 1, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, entry TEXT, status TEXT DEFAULT 'stopped',
  pid INTEGER, started_at REAL, auto_restart INTEGER DEFAULT 1, req_hash TEXT, github_url TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS envs(project_id INTEGER, k TEXT, v TEXT, PRIMARY KEY(project_id, k));
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_id TEXT, method TEXT, amount REAL,
  trx_id TEXT, status TEXT DEFAULT 'pending', created_at REAL, decided_by INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS ux_trx ON payments(trx_id) WHERE status != 'rejected';
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS vp_tokens(
  token TEXT PRIMARY KEY, user_id INTEGER, plan_id TEXT, days INTEGER, created_at REAL, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS vp_claims(
  user_id INTEGER PRIMARY KEY, plan_id TEXT, claimed_at REAL);
"""

USER_COLS = {"username", "first_name", "balance", "referred_by", "ref_count", "ref_rewarded", "verified",
             "banned", "plan_id", "plan_expires", "trial_started", "exp_notified", "trial_notified", "lang"}
PROJECT_COLS = {"name", "entry", "status", "pid", "started_at", "auto_restart", "req_hash", "github_url"}
PLAN_COLS = {"name", "price", "days", "max_projects", "ram_mb", "cpu_pct", "disk_mb", "auto_restart", "active", "sort"}


# ---------------------------------------------------------------- primitives
def db_q(sql, args=()):
    with _db_lock:
        return [dict(r) for r in _db_conn.execute(sql, args).fetchall()]


def db_q1(sql, args=()):
    r = db_q(sql, args)
    return r[0] if r else None


def db_val(sql, args=()):
    r = db_q1(sql, args)
    return list(r.values())[0] if r else 0


def db_x(sql, args=()):
    with _db_lock:
        return _db_conn.execute(sql, args)


def _db_upd(table, key, id_, allowed, kw):
    kw = {k: v for k, v in kw.items() if k in allowed}
    if kw:
        sets = ",".join(f"{k}=?" for k in kw)
        db_x(f"UPDATE {table} SET {sets} WHERE {key}=?", (*kw.values(), id_))


def db_init():
    with _db_lock:
        _db_conn.executescript(SCHEMA)
        try:   # language column (existing users keep Bengali; new users are asked on /start)
            _db_conn.execute("ALTER TABLE users ADD COLUMN lang TEXT")
            _db_conn.execute("UPDATE users SET lang='bn'")
        except sqlite3.OperationalError:
            pass
    if not db_q1("SELECT 1 FROM plans LIMIT 1"):
        for r in SEED_PLANS:
            db_x("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?)", r)
    for k, v in DEFAULT_SETTINGS.items():
        db_x("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
    # one-time upgrades for databases created by an older version (never re-applied, so admin edits stick)
    if not db_q1("SELECT 1 FROM settings WHERE k='_m_student'"):
        if not db_q1("SELECT 1 FROM plans WHERE id='student'"):
            db_x("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?)", [r for r in SEED_PLANS if r[0] == "student"][0])
        db_x("INSERT INTO settings VALUES('_m_student','1')")
    if not db_q1("SELECT 1 FROM settings WHERE k='_m_support'"):
        if not db_get_setting("support").strip():
            db_set_setting("support", DEFAULT_SETTINGS["support"])
        db_x("INSERT INTO settings VALUES('_m_support','1')")
    if not db_q1("SELECT 1 FROM settings WHERE k='_m_support2'"):
        if db_get_setting("support").strip() in ("", "@Abdulahad1234577"):
            db_set_setting("support", DEFAULT_SETTINGS["support"])
        db_x("INSERT INTO settings VALUES('_m_support2','1')")
    # channel join verification -> official channel (one-time; later edits from the Admin Panel stick)
    if not db_q1("SELECT 1 FROM settings WHERE k='_m_channel_kdj'"):
        db_set_setting("channels", DEFAULT_SETTINGS["channels"])
        db_x("INSERT INTO settings VALUES('_m_channel_kdj','1')")
    # premium welcome text (only if the admin never customised the old default)
    if not db_q1("SELECT 1 FROM settings WHERE k='_m_welcome_v2'"):
        if db_get_setting("welcome_text").strip() == _WELCOME_V1.strip():
            db_set_setting("welcome_text", DEFAULT_WELCOME)
        db_x("INSERT INTO settings VALUES('_m_welcome_v2','1')")


# ---------------------------------------------------------------- settings
def db_get_setting(k, default=""):
    r = db_q1("SELECT v FROM settings WHERE k=?", (k,))
    return r["v"] if r else DEFAULT_SETTINGS.get(k, default)


def db_set_setting(k, v):
    db_x("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, str(v)))


# ---------------------------------------------------------------- users
def db_upsert_user(tu, referred_by=None) -> bool:
    """Returns True if the user was newly created."""
    if db_q1("SELECT id FROM users WHERE id=?", (tu.id,)):
        db_x("UPDATE users SET username=?, first_name=? WHERE id=?", (tu.username, tu.first_name, tu.id))
        return False
    if referred_by == tu.id or (referred_by and not db_q1("SELECT 1 FROM users WHERE id=?", (referred_by,))):
        referred_by = None
    cur = db_x("INSERT OR IGNORE INTO users(id,username,first_name,joined_at,referred_by) VALUES(?,?,?,?,?)",
            (tu.id, tu.username, tu.first_name, time.time(), referred_by))
    return cur.rowcount == 1


def db_get_user(uid):
    return db_q1("SELECT * FROM users WHERE id=?", (uid,))


def db_update_user(user_id, /, **kw):
    _db_upd("users", "id", user_id, USER_COLS, kw)


def db_add_balance(uid, amount):
    db_x("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))


def db_charge_balance(uid, amount) -> bool:
    return db_x("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?", (amount, uid, amount)).rowcount == 1


def db_claim_referral(uid, amount):
    """Pays the referrer once. Returns referrer id or None."""
    with _db_lock:
        u = db_get_user(uid)
        if not u or not u["referred_by"] or u["ref_rewarded"]:
            return None
        _db_conn.execute("BEGIN")
        try:
            cur = _db_conn.execute("UPDATE users SET ref_rewarded=1 WHERE id=? AND ref_rewarded=0", (uid,))
            if cur.rowcount != 1:
                _db_conn.execute("ROLLBACK")
                return None
            _db_conn.execute("UPDATE users SET balance=balance+?, ref_count=ref_count+1 WHERE id=?",
                          (amount, u["referred_by"]))
            _db_conn.execute("COMMIT")
        except Exception:
            _db_conn.execute("ROLLBACK")
            raise
        return u["referred_by"]


def db_all_user_ids():
    return [r["id"] for r in db_q("SELECT id FROM users WHERE banned=0")]


# ---------------------------------------------------------------- plans
def db_get_plan(pid):
    return db_q1("SELECT * FROM plans WHERE id=?", (pid,))


def db_list_plans(active_only=False, paid_only=False):
    sql = "SELECT * FROM plans WHERE 1=1"
    if active_only:
        sql += " AND active=1"
    if paid_only:
        sql += " AND id!='free'"
    return db_q(sql + " ORDER BY sort, price")


def db_update_plan(plan_id, /, **kw):
    _db_upd("plans", "id", plan_id, PLAN_COLS, kw)


def db_add_plan(pid, name, price, days, max_projects, ram, cpu, disk):
    db_x("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,1,1,?)",
      (pid, name, price, days, max_projects, ram, cpu, disk, int(time.time()) % 100000))


def db_delete_plan(pid):
    db_x("DELETE FROM plans WHERE id=? AND id!='free'", (pid,))


def db_plan_state(u):
    """Effective plan of a user: paid plan if valid, otherwise FREE (7-day trial that starts on first run)."""
    now = time.time()
    if u["plan_id"] != "free" and u["plan_expires"] and u["plan_expires"] > now:
        p = db_get_plan(u["plan_id"])
        if p:
            return {"plan": p, "paid": True, "active": True, "expires": u["plan_expires"]}
    free = db_get_plan("free")
    ts = u["trial_started"]
    if ts is None:
        return {"plan": free, "paid": False, "active": True, "expires": None}
    exp = ts + free["days"] * 86400
    return {"plan": free, "paid": False, "active": exp > now, "expires": exp}


def db_activate_plan(uid, plan_id, days=None):
    plan, u, now = db_get_plan(plan_id), db_get_user(uid), time.time()
    same = u["plan_id"] == plan_id and u["plan_expires"] and u["plan_expires"] > now
    exp = (u["plan_expires"] if same else now) + (days or plan["days"]) * 86400
    db_update_user(uid, plan_id=plan_id, plan_expires=exp, exp_notified=0)
    return exp


# ---------------------------------------------------------------- projects
def db_create_project(uid, name):
    cur = db_x("INSERT INTO projects(user_id,name,created_at) VALUES(?,?,?)", (uid, name, time.time()))
    return cur.lastrowid


def db_get_project(pid):
    return db_q1("SELECT * FROM projects WHERE id=?", (pid,))


def db_user_projects(uid):
    return db_q("SELECT * FROM projects WHERE user_id=? ORDER BY id", (uid,))


def db_update_project(project_id, /, **kw):
    _db_upd("projects", "id", project_id, PROJECT_COLS, kw)


def db_delete_project(pid):
    db_x("DELETE FROM envs WHERE project_id=?", (pid,))
    db_x("DELETE FROM projects WHERE id=?", (pid,))


def db_running_count(uid):
    return db_val("SELECT COUNT(*) FROM projects WHERE user_id=? AND status='running'", (uid,))


def db_projects_by_status(status):
    return db_q("SELECT * FROM projects WHERE status=?", (status,))


def db_get_envs(pid):
    return db_q("SELECT k, v FROM envs WHERE project_id=? ORDER BY k", (pid,))


def db_set_env(pid, k, v):
    db_x("INSERT OR REPLACE INTO envs VALUES(?,?,?)", (pid, k, v))


def db_del_env(pid, k):
    db_x("DELETE FROM envs WHERE project_id=? AND k=?", (pid, k))


# ---------------------------------------------------------------- payments
def db_trx_exists(trx):
    return db_q1("SELECT 1 FROM payments WHERE trx_id=? AND status!='rejected'", (trx,)) is not None


def db_add_payment(uid, plan_id, method, amount, trx):
    return db_x("INSERT INTO payments(user_id,plan_id,method,amount,trx_id,created_at) VALUES(?,?,?,?,?,?)",
             (uid, plan_id, method, amount, trx, time.time())).lastrowid


def db_get_payment(pid):
    return db_q1("SELECT * FROM payments WHERE id=?", (pid,))


def db_pending_payments():
    return db_q("SELECT * FROM payments WHERE status='pending' ORDER BY id")


def db_user_pending_count(uid):
    return db_val("SELECT COUNT(*) FROM payments WHERE user_id=? AND status='pending'", (uid,))


# ---------------------------------------------------------------- stats / backup
def db_stats():
    now = time.time()
    return {
        "users": db_val("SELECT COUNT(*) FROM users"),
        "verified": db_val("SELECT COUNT(*) FROM users WHERE verified=1"),
        "banned": db_val("SELECT COUNT(*) FROM users WHERE banned=1"),
        "today": db_val("SELECT COUNT(*) FROM users WHERE joined_at>?", (now - 86400,)),
        "paid": db_val("SELECT COUNT(*) FROM users WHERE plan_id!='free' AND plan_expires>?", (now,)),
        "projects": db_val("SELECT COUNT(*) FROM projects"),
        "running": db_val("SELECT COUNT(*) FROM projects WHERE status='running'"),
        "pending": db_val("SELECT COUNT(*) FROM payments WHERE status='pending'"),
        "revenue": db_val("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'"),
    }


def db_backup_to(path):
    with _db_lock:
        dst = sqlite3.connect(str(path))
        _db_conn.backup(dst)
        dst.close()


db_init()


# ==============================================================================
# 5. CORE  (bot instance, routers, guards, UI helpers, channel check)
# ==============================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pyhost")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN সেট করা নেই। .env ফাইলে BOT_TOKEN=... দিন।")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=24)

M_PANEL = "🎛 কন্ট্রোল প্যানেল"
M_FILES = "📁 ফাইল ম্যানেজার"
M_UPGRADE = "💎 প্ল্যান আপগ্রেড"
M_ACCOUNT = "👤 আমার অ্যাকাউন্ট"
M_REFER = "🎁 রেফার ও আর্ন"
M_DOCS = "📖 ডকুমেন্টেশন"
M_SUPPORT = "🛰 সাপোর্ট হাব"
M_ADMIN = "🛠 অ্যাডমিন প্যানেল"
M_LANG = "🌐 ভাষা / Language"

STATE = {}   # uid -> {"action": str, ...}
CACHE = {}   # misc per-user ephemeral data (file manager, entry list ...)
MENU = {}    # reply-keyboard text -> handler
STATES = {}  # state action -> handler
CB = {}      # callback head -> handler


def extra_admins():
    """Admins added from the Admin Panel (stored in DB)."""
    return {int(x) for x in db_get_setting("admins").replace(" ", "").split(",") if x.isdigit()}


def is_owner(uid):
    """Main admins = ADMIN_IDS in .env. Only they can manage admins and take DB backups."""
    return uid in ADMIN_IDS


def is_admin(uid):
    return uid in ADMIN_IDS or uid in extra_admins()


def set_state(user_id, action, /, **data):
    # user_id/action are positional-only so callers can pass uid=... as extra state data
    STATE[user_id] = {"action": action, **data}


def get_state(uid):
    return STATE.get(uid)


def clear_state(uid):
    STATE.pop(uid, None)


# ---------------------------------------------------------------- UI helpers
def kb(*rows):
    """Build inline keyboards with Telegram Bot API 9.4 button colors.

    Keeps every existing label, callback, URL, and row layout unchanged.
    Colors are chosen from the button's meaning; older pyTelegramBotAPI
    versions gracefully fall back to normal buttons.
    """
    mk = types.InlineKeyboardMarkup()

    def _style_for(text, data):
        label = str(text).casefold()
        action = str(data).casefold()
        red_words = ("back", "cancel", "delete", "remove", "ban", "stop", "off", "বন্ধ", "বাতিল", "মুছ", "পেছনে", "ডিলিট")
        green_words = ("buy", "upgrade", "confirm", "start", "restart", "run", "submit", "pay", "select plan", "purchase", "চালু", "রিস্টার্ট", "কিনুন", "নিশ্চিত", "প্ল্যান", "আপগ্রেড")
        if any(word in label or word in action for word in red_words):
            return "danger"
        if any(word in label or word in action for word in green_words):
            return "success"
        return "primary"

    for row in rows:
        buttons = []
        for t, d in row:
            style = _style_for(t, d)
            try:
                if str(d).startswith("http"):
                    button = types.InlineKeyboardButton(t, url=d, style=style)
                else:
                    button = types.InlineKeyboardButton(t, callback_data=d, style=style)
            except TypeError:
                # Older pyTelegramBotAPI releases don't expose Bot API 9.4 styles.
                if str(d).startswith("http"):
                    button = types.InlineKeyboardButton(t, url=d)
                else:
                    button = types.InlineKeyboardButton(t, callback_data=d)
            buttons.append(button)
        mk.row(*buttons)
    return mk


def main_menu(uid):
    # Telegram Bot API 9.4+: colored Reply Keyboard buttons.
    # Keep the existing labels, layout, and handlers unchanged.
    try:
        mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, is_persistent=True,
                                       input_field_placeholder="✨ মেনু থেকে বেছে নিন")
    except TypeError:   # older pyTelegramBotAPI
        mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)

    def _styled_button(label, style="primary"):
        try:
            return types.KeyboardButton(label, style=style)
        except TypeError:
            # Older pyTelegramBotAPI versions do not support the style field.
            # Upgrade pyTelegramBotAPI to a Bot API 9.4-compatible version
            # to enable colors; retain a working keyboard on older versions.
            return types.KeyboardButton(label)

    mk.row(_styled_button(M_PANEL, "primary"), _styled_button(M_FILES, "primary"))
    mk.row(_styled_button(M_UPGRADE, "success"), _styled_button(M_ACCOUNT, "primary"))
    mk.row(_styled_button(M_REFER, "success"), _styled_button(M_DOCS, "primary"))
    mk.row(_styled_button(M_SUPPORT, "primary"), _styled_button(M_LANG, "primary"))
    if is_admin(uid):
        mk.row(_styled_button(M_ADMIN, "danger"))
    return mk


def ack(call, text=None, alert=False):
    try:
        bot.answer_callback_query(call.id, tr(text, get_lang(call.from_user.id)), show_alert=alert)
    except Exception:  # already answered / expired
        pass


def show(obj, text, markup=None):
    """Edit the message when triggered from a button, otherwise send a new one."""
    if isinstance(obj, types.CallbackQuery):
        try:
            bot.edit_message_text(text, obj.message.chat.id, obj.message.message_id, reply_markup=markup,
                                  disable_web_page_preview=True)
            return
        except ApiTelegramException as e:
            if "message is not modified" in str(e):
                return
        except Exception:  # noqa
            pass
        bot.send_message(obj.message.chat.id, text, reply_markup=markup, disable_web_page_preview=True)
    else:
        bot.send_message(obj.chat.id, text, reply_markup=markup, disable_web_page_preview=True)


def notify(uid, text, markup=None):
    try:
        bot.send_message(uid, text, reply_markup=markup, disable_web_page_preview=True)
        return True
    except Exception as e:  # noqa
        log.info("notify %s failed: %s", uid, e)
        return False


def notify_admins(text, markup=None):
    for a in ADMIN_IDS | extra_admins():
        notify(a, text, markup)


_bot_username = {}


def bot_username():
    if "u" not in _bot_username:
        _bot_username["u"] = bot.get_me().username
    return _bot_username["u"]


# ---------------------------------------------------------------- premium look & feel
DIV = "─────  ✦  ─────"


def head(icon, title, sub=""):
    """Consistent premium screen header (title / sub must already be HTML-safe)."""
    return f"✦ {icon} <b>{title}</b>\n" + (f"<i>{sub}</i>\n" if sub else "") + DIV + "\n"


def bar(val, mx, n=10, pct=True):
    """Colored gauge like 🟩🟩🟩⬜⬜⬜⬜⬜ 37%  (green -> amber -> red as it fills up)."""
    try:
        r = max(0.0, min(1.0, float(val) / float(mx)))
    except (TypeError, ValueError, ZeroDivisionError):
        r = 0.0
    k = int(round(r * n))
    tone = "🟩" if r < 0.6 else "🟨" if r < 0.85 else "🟥"
    return tone * k + "⬜" * (n - k) + (f" <b>{r * 100:.0f}%</b>" if pct else "")


def hosting_ram():
    """RAM that the hosting really gives this bot (container / cgroup limit).
    Returns (used_bytes, limit_bytes), or None when no separate limit is set (the whole machine is yours)."""
    def rd(path):
        try:
            return Path(path).read_text().strip()
        except Exception:  # noqa
            return None

    total = psutil.virtual_memory().total
    for lim_f, cur_f, stat_f, key in (
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.stat", "inactive_file"),   # cgroup v2
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes",
         "/sys/fs/cgroup/memory/memory.stat", "total_inactive_file"),                                                  # cgroup v1
    ):
        lim, cur = rd(lim_f), rd(cur_f)
        if not lim or not cur or not lim.isdigit() or not cur.isdigit():
            continue
        limit, used = int(lim), int(cur)
        if limit >= total:        # "max" / unlimited / bigger than the machine -> no real limit
            continue
        for line in (rd(stat_f) or "").splitlines():      # do not count reclaimable file cache as used
            k, _, v = line.partition(" ")
            if k == key and v.isdigit():
                used = max(0, used - int(v))
        return used, limit
    return None


def plan_card(p):
    bots = str(p["max_projects"]).translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))
    s = (f"<b>{esc(p['name'])}</b>\n💸 <b>৳{p['price']:g}</b>  •  ⏳ {p['days']} দিন\n"
         f"<blockquote>🤖 {bots}টি বট চালাতে পারবেন\n"
         f"💾 RAM: {p['ram_mb']}MB  •  🧠 CPU: {p['cpu_pct']}%\n"
         f"💽 ডিস্ক: {p['disk_mb']}MB")
    return s + ("\n♻️ অটো-রিস্টার্ট ✔</blockquote>" if p["auto_restart"] else "</blockquote>")


def plan_left(st):
    if st["paid"]:
        return f"{days_left(st['expires'])} দিন"
    if st["expires"] is None:
        return f"ট্রায়াল শুরু হয়নি (প্রথম চালুর পর {st['plan']['days']} দিন)"
    if st["active"]:
        return f"ট্রায়াল: {fmt_dur(st['expires'] - time.time())} বাকি"
    return "❌ মেয়াদ শেষ — প্ল্যান কিনুন"


# ---------------------------------------------------------------- channel verification
def _norm_channel(c):
    """'@name' / 'name' / 'https://t.me/name' / 't.me/name'  ->  '@name'"""
    c = re.sub(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/", "", (c or "").strip(), flags=re.I)
    c = c.split("?")[0].strip("/").lstrip("@").strip()
    return "@" + c if c else ""


def channels():
    return [ch for ch in (_norm_channel(c) for c in db_get_setting("channels").split(",")) if ch]


_member_cache = {}


_MEMBER_OK = ("member", "administrator", "creator")
_ch_err = {}      # channel -> last error text (shown to admins)
_warn_ts = {}


def _channel_status(uid):
    """Returns (missing, broken): channels the user has NOT joined / channels the bot cannot check."""
    missing, broken = [], []
    for ch in channels():
        try:
            m = bot.get_chat_member(ch, uid)
            if m.status not in _MEMBER_OK and not (m.status == "restricted" and getattr(m, "is_member", False)):
                missing.append(ch)
        except Exception as e:  # bot not admin in channel, wrong username...
            log.warning("membership check failed for %s: %s", ch, e)
            _ch_err[ch] = str(e)
            broken.append(ch)
    return missing, broken


def _warn_channel_admins(broken):
    """Tell the admins (at most once an hour) that the bot cannot check a channel — otherwise users get stuck silently."""
    now = time.time()
    if now - _warn_ts.get("ch", 0) < 3600:
        return
    _warn_ts["ch"] = now
    lst = "\n".join(f"• <b>{esc(c)}</b> — <code>{esc(_ch_err.get(c, '')[:120])}</code>" for c in broken)
    notify_admins(head("⚠️", "চ্যানেল ভেরিফিকেশন কাজ করছে না", "ইউজাররা ভেরিফাই করতে পারছে না")
                  + f"<blockquote>{lst}</blockquote>\n\n"
                    "<b>সমাধান:</b>\n"
                    "1️⃣ বটকে ওই চ্যানেলে <b>Admin</b> বানান\n"
                    "2️⃣ চ্যানেলের @username ঠিক আছে কিনা দেখুন (🛠 অ্যাডমিন → ⚙️ সেটিংস)")


def is_member(uid):
    if not channels():
        return True
    t = _member_cache.get(uid)
    if t and time.time() - t < 180:
        return True
    missing, broken = _channel_status(uid)
    if missing or broken:
        _member_cache.pop(uid, None)
        if broken:
            _warn_channel_admins(broken)
        return False
    _member_cache[uid] = time.time()
    return True


def _join_screen(missing=None):
    """(text, keyboard) of the join-verification screen. `missing` = channels still to join (after a failed check)."""
    chs = channels()
    todo = [c for c in chs if missing is None or c in missing]
    lines = [(f"✅ <b>{esc(c)}</b> — জয়েন করা আছে" if missing is not None and c not in missing
              else f"📢 <b>{esc(c)}</b>") for c in chs]
    warn = "❗ <b>আপনি এখনো সব চ্যানেলে জয়েন করেননি!</b> জয়েন করে আবার ✅ চাপুন।\n\n" if missing is not None else ""
    txt = (head("🔐", "চ্যানেল ভেরিফিকেশন", "বট ব্যবহারের আগে একটি ছোট্ট ধাপ")
           + warn
           + "বট ব্যবহার শুরু করতে আগে আমাদের অফিসিয়াল চ্যানেলে জয়েন করুন।\n\n"
           + "<blockquote>" + "\n".join(lines) + "</blockquote>\n\n"
           + "<b>কীভাবে ভেরিফাই করবেন?</b>\n"
             "1️⃣ নিচের <b>📢 জয়েন করুন</b> বাটনে চাপ দিয়ে চ্যানেলে জয়েন করুন\n"
             "2️⃣ এখানে ফিরে এসে <b>✅ ভেরিফাই করুন</b> বাটনে চাপুন\n\n"
             "<blockquote>🎁 জয়েন করলেই ফ্রি প্ল্যান, রেফার রিওয়ার্ড ও সব আপডেট আনলক হবে!</blockquote>")
    rows = [[(f"📢 জয়েন করুন {c}", f"https://t.me/{c.lstrip('@')}")] for c in todo]
    rows.append([("✅ ভেরিফাই করুন", "chk")])
    return txt, kb(*rows)


def send_join(chat_id, missing=None):
    txt, mk = _join_screen(missing)
    bot.send_message(chat_id, txt, reply_markup=mk)


# ---------------------------------------------------------------- guard + routers
def guarded(fn, gate=True):
    @functools.wraps(fn)
    def wrapper(obj, *a):
        is_cb = isinstance(obj, types.CallbackQuery)
        u = obj.from_user
        chat_id = obj.message.chat.id if is_cb else obj.chat.id
        try:
            db_upsert_user(u)
            row = db_get_user(u.id)
            if row["banned"]:
                if is_cb:
                    ack(obj, "🚫 আপনাকে ব্যান করা হয়েছে", True)
                else:
                    bot.send_message(chat_id, "🚫 আপনাকে ব্যান করা হয়েছে।")
                return
            if gate and not is_admin(u.id) and not is_member(u.id):
                clear_state(u.id)
                send_join(chat_id)
                return
            return fn(obj, *a)
        except ApiTelegramException as e:
            if "message is not modified" not in str(e):
                log.warning("Telegram API error: %s", e)
        except Exception:  # noqa
            log.exception("handler error")
            try:
                bot.send_message(chat_id, "⚠️ একটি ত্রুটি হয়েছে, আবার চেষ্টা করুন।")
            except Exception:  # noqa
                pass
        finally:
            if is_cb:
                ack(obj)
    return wrapper


def cb(head, gate=True):
    def deco(fn):
        CB[head] = guarded(_cb_animated(head, fn), gate)
        return fn
    return deco


# ---------------------------------------------------------------- animated loading screens
# Light-weight "premium" animations, two kinds:
#   1) quick   — 4 short frames (~1 s): menu buttons, /start, /admin, typed requests and a few screens
#   2) working — a spinner for long jobs (upload, install, start, git sync, backup ...). All spinners are
#      driven by ONE shared background thread that edits each message only once per second, and a
#      spinner for a slow button only appears if the button really takes longer than 0.8 s.
# Safety: per-user cool-down, a cap on simultaneous animations, any Telegram error just stops the animation.
# Turn everything off with  ANIMATIONS=0  in .env  (quick-frame speed: ANIM_DELAY=0.28 seconds).
import contextlib

ANIMATIONS = os.getenv("ANIMATIONS", "1").strip() != "0"
try:
    ANIM_DELAY = float(os.getenv("ANIM_DELAY", "0.28"))
except ValueError:
    ANIM_DELAY = 0.28
ANIM_MAX = 40            # max working spinners at the same time (extra ones are simply skipped)
ANIM_TICK = 1.0          # seconds between two frames of a working spinner
ANIM_MAX_AGE = 30 * 60   # safety: a spinner never runs longer than this
_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ANIM_STYLES = {          # key -> (caption, spinner frames, filled block, empty block)
    # main-menu buttons
    M_PANEL:   ("Opening Control Panel",     _BRAILLE,                   "▰", "▱"),
    M_FILES:   ("Opening File Manager",      ["📁", "📂", "📁", "📂"],   "█", "░"),
    M_UPGRADE: ("Loading premium plans",     ["💎", "✨", "💠", "✨"],   "◆", "◇"),
    M_ACCOUNT: ("Fetching your account",     "◐◓◑◒",                    "●", "○"),
    M_REFER:   ("Preparing your rewards",    ["🎁", "🎉", "🎊", "🎉"],   "■", "□"),
    M_DOCS:    ("Opening documentation",     ["📕", "📗", "📘", "📙"],   "▰", "▱"),
    M_SUPPORT: ("Connecting to Support Hub", ["📡", "🛰", "📡", "🛰"],   "▇", "▁"),
    M_ADMIN:   ("Verifying admin access",    ["🔒", "🔐", "🔑", "🔓"],   "▰", "▱"),
    M_LANG:    ("Loading languages",         ["🌍", "🌎", "🌏", "🌎"],   "●", "○"),
    # commands, typed requests and quick screens
    "start_cmd": ("Starting up",                _BRAILLE,                 "▰", "▱"),
    "req":       ("Processing your request",    _BRAILLE,                 "▰", "▱"),
    "pl":        ("Fetching live logs",         ["📋", "📜", "📋", "📜"], "▰", "▱"),
    "plive":     ("Starting live monitor",      ["📡", "🛰", "📡", "🛰"], "▇", "▁"),
    "fup":       ("Preparing upload mode",      ["📤", "⬆️", "📤", "⬆️"], "▰", "▱"),
    "ppm":       ("Opening Package Manager",    ["📦", "🧩", "📦", "🧩"], "◆", "◇"),
    "pg":        ("Connecting to GitHub",       ["🐙", "🔗", "🐙", "🔗"], "▰", "▱"),
    "pev":       ("Loading environment",        ["🔐", "🗝", "🔐", "🗝"], "●", "○"),
    "pdb":       ("Opening data vault",         ["🗄", "💾", "🗄", "💾"], "█", "░"),
    "buy":       ("Preparing payment",          ["💳", "💸", "💳", "💸"], "■", "□"),
    "a:stats":   ("Collecting server stats",    ["📊", "📈", "📊", "📉"], "▰", "▱"),
    # long jobs (working spinners)
    "work":       ("Working on it",             _BRAILLE,                 "▰", "▱"),
    "upload":     ("Uploading file",            ["📤", "⬆️", "📤", "⬆️"], "▰", "▱"),
    "install":    ("Installing packages",       ["📦", "⚙️", "🔧", "⚙️"], "▰", "▱"),
    "start":      ("Starting project",          ["🚀", "✨", "🚀", "✨"], "▰", "▱"),
    "git":        ("Syncing repository",        ["🐙", "🔄", "🐙", "🔄"], "▰", "▱"),
    "backup":     ("Creating backup",           ["💾", "🗜", "💾", "🗜"], "█", "░"),
    "fullbackup": ("Building full server backup", ["🗃", "🗜", "🗃", "🗜"], "█", "░"),
    "fz":         ("Extracting archive",        ["🗂", "📂", "🗂", "📂"], "▰", "▱"),
}


def _del_msg(chat_id, mid):
    if mid:
        try:
            bot.delete_message(chat_id, mid)
        except Exception:  # noqa
            pass


# ---- 1) quick frames ---------------------------------------------------------------------------------
_QUICK_LAST = {}
_QUICK_BUSY = [0]


def _quick_ok(uid):
    now = time.time()
    if not ANIMATIONS or _QUICK_BUSY[0] >= 12 or now - _QUICK_LAST.get(uid, 0) < 2.0:
        return False
    if len(_QUICK_LAST) > 5000:
        _QUICK_LAST.clear()
    _QUICK_LAST[uid] = now
    return True


def _anim_play(chat_id, key, uid=None):
    """Plays the quick loading animation for `key`; returns the message id (to delete it later) or None."""
    st = ANIM_STYLES.get(key)
    if not st or not _quick_ok(uid if uid is not None else chat_id):
        return None
    cap, spin, on, off = st
    mid = None
    _QUICK_BUSY[0] += 1
    try:
        for i, pct in enumerate((17, 50, 83, 100)):
            n = round(pct / 100 * 6)
            txt = f"{spin[i % len(spin)]} <b>{cap}...</b>\n\n{on * n}{off * (6 - n)} <b>{pct}%</b>"
            if mid is None:
                mid = bot.send_message(chat_id, txt).message_id
            else:
                bot.edit_message_text(txt, chat_id, mid)
            time.sleep(ANIM_DELAY)
    except Exception as e:  # noqa  (flood limit etc. — just skip the rest of the animation)
        log.debug("animation stopped: %s", e)
    finally:
        _QUICK_BUSY[0] -= 1
    return mid


# ---- 2) working spinners (one shared thread) -----------------------------------------------------------
class _Anim:
    __slots__ = ("chat", "mid", "style", "body", "own", "t0", "next_at", "tick", "lock", "alive")


_ANIMS = {}
_ANIM_LOCK = threading.Lock()
_ANIM_WAKE = threading.Event()
_ANIM_THREAD = []


def _work_text(e):
    cap, spin, on, off = e.style
    bar = "".join(on if (i - e.tick) % 8 < 3 else off for i in range(8))
    txt = f"{spin[e.tick % len(spin)]} <b>{cap}...</b>\n\n{bar}  ⏱ {int(time.time() - e.t0)}s"
    return txt + (f"\n\n{e.body}" if e.body else "")


def _anim_loop():
    while True:
        try:
            with _ANIM_LOCK:
                items = list(_ANIMS.values())
            if not items:
                _ANIM_WAKE.clear()
                with _ANIM_LOCK:
                    busy = bool(_ANIMS)
                if not busy:
                    _ANIM_WAKE.wait()
                continue
            now = time.time()
            for e in items:
                if now < e.next_at:
                    continue
                with e.lock:
                    if not e.alive:
                        continue
                    if now - e.t0 > ANIM_MAX_AGE:
                        e.alive = False
                    else:
                        try:
                            txt = _work_text(e)
                            if e.mid is None:
                                e.mid = bot.send_message(e.chat, txt).message_id
                            else:
                                bot.edit_message_text(txt, e.chat, e.mid)
                        except ApiTelegramException as ex:
                            if "message is not modified" not in str(ex):
                                e.alive = False
                        except Exception:  # noqa
                            e.alive = False
                        e.tick += 1
                        e.next_at = time.time() + ANIM_TICK
                if not e.alive:
                    with _ANIM_LOCK:
                        _ANIMS.pop(id(e), None)
                    if e.own:
                        _del_msg(e.chat, e.mid)
        except Exception:  # noqa  (the animator must never die)
            log.debug("animator error", exc_info=True)
        time.sleep(0.35)


def anim_begin(chat_id, key="work", mid=None, body="", delay=0.0):
    """Starts a working spinner and returns a handle for anim_end().
    mid=None  -> the spinner sends (and later deletes) its own message after `delay` seconds;
    mid=<id>  -> it animates the caller's message: call anim_end / anim_end_msg BEFORE the final edit."""
    if not ANIMATIONS:
        return None
    with _ANIM_LOCK:
        if len(_ANIMS) >= ANIM_MAX:
            return None
        e = _Anim()
        e.chat, e.mid, e.body, e.own = chat_id, mid, body, mid is None
        e.style = ANIM_STYLES.get(key) or ANIM_STYLES["work"]
        e.t0 = time.time()
        e.tick, e.lock, e.alive = 0, threading.Lock(), True
        e.next_at = e.t0 + (delay if e.own else ANIM_TICK)
        _ANIMS[id(e)] = e
        if not _ANIM_THREAD:
            t = threading.Thread(target=_anim_loop, daemon=True, name="anim-loop")
            _ANIM_THREAD.append(t)
            t.start()
    _ANIM_WAKE.set()
    return e


def anim_end(e):
    """Stops a spinner (no more edits happen after this returns) and removes its own message."""
    if e is None:
        return
    with e.lock:
        e.alive = False
        mid = e.mid if e.own else None
    with _ANIM_LOCK:
        _ANIMS.pop(id(e), None)
    _del_msg(e.chat, mid)


def anim_end_msg(chat_id, mid):
    """Same as anim_end, for a spinner that animates the caller's message (found by chat id + message id)."""
    with _ANIM_LOCK:
        found = [e for e in _ANIMS.values() if e.chat == chat_id and e.mid == mid and not e.own]
    for e in found:
        anim_end(e)


@contextlib.contextmanager
def work(chat_id, key="work", delay=0.0):
    e = anim_begin(chat_id, key, delay=delay)
    try:
        yield
    finally:
        anim_end(e)


# ---- hooks: inline buttons, commands, menu --------------------------------------------------------------
_CB_QUICK = {"pl", "plive", "fup", "ppm", "pg", "pev", "pdb", "buy", "a:stats"}   # always show the quick frames
_CB_SKIP = {"up", "lang", "langm", "chk", "ploff", "px", "par"}                   # instant toggles / back buttons


def _cb_animated(head, fn):
    @functools.wraps(fn)
    def w(call, a):
        if not ANIMATIONS or head in _CB_SKIP:
            return fn(call, a)
        key = f"a:{a[0]}" if head == "a" and a else head
        chat_id = call.message.chat.id
        if key in _CB_QUICK:
            mid = _anim_play(chat_id, key, call.from_user.id)
            try:
                return fn(call, a)
            finally:
                _del_msg(chat_id, mid)
        e = anim_begin(chat_id, key if key in ANIM_STYLES else "work", delay=0.8)   # shows only if the button is slow
        try:
            return fn(call, a)
        finally:
            anim_end(e)
    return w


def _quick_cmd(key, admin_only=False):
    def deco(fn):
        @functools.wraps(fn)
        def w(m, *a):
            mid = None
            if not (admin_only and not is_admin(m.from_user.id)):
                mid = _anim_play(m.chat.id, key, m.from_user.id)
            try:
                return fn(m, *a)
            finally:
                _del_msg(m.chat.id, mid)
        return w
    return deco


def menu(text):
    def deco(fn):
        @functools.wraps(fn)
        def animated(m, *a):
            mid = None if (text == M_ADMIN and not is_admin(m.from_user.id)) else _anim_play(m.chat.id, text, m.from_user.id)
            try:
                return fn(m, *a)
            finally:
                _del_msg(m.chat.id, mid)
        MENU[text] = guarded(animated)
        return fn
    return deco


def state(action):
    def deco(fn):
        STATES[action] = guarded(fn)
        return fn
    return deco


@bot.callback_query_handler(func=lambda c: True)
def _on_callback(call):
    parts = (call.data or "").split(":")
    fn = CB.get(parts[0])
    if fn:
        fn(call, parts[1:])
    else:
        ack(call)


@bot.message_handler(content_types=["text"], func=lambda m: not (m.text or "").startswith("/"))
def _on_text(m):
    uid = m.from_user.id
    t = _MENU_REV.get(m.text.strip(), m.text.strip())
    if t in MENU:
        clear_state(uid)
        return MENU[t](m)
    st = get_state(uid)
    if st and st["action"] in STATES:
        mid = None if st["action"] in ("upload", "data_up", "pip_in") else _anim_play(m.chat.id, "req", uid)
        try:
            return STATES[st["action"]](m, st)
        finally:
            _del_msg(m.chat.id, mid)
    guarded(lambda mm: bot.send_message(mm.chat.id, "👇 মেনু থেকে একটি অপশন বেছে নিন।",
                                        reply_markup=main_menu(mm.from_user.id)))(m)


@bot.message_handler(content_types=["document", "photo", "video", "audio", "voice", "animation", "sticker"])
def _on_media(m):
    st = get_state(m.from_user.id)
    if st and st["action"] in STATES:
        return STATES[st["action"]](m, st)
    guarded(lambda mm: bot.send_message(
        mm.chat.id, "📤 ফাইল আপলোড করতে: 📁 <b>ফাইল ম্যানেজার</b> → প্রজেক্ট → 📤 আপলোড।"))(m)


# ==============================================================================
# 5b. LANGUAGE  (Bengali = default, English = per-user option)
# ==============================================================================
# All bot texts are written in Bengali in the code. When a user picks English, every outgoing message /
# button / alert is translated on the fly with the phrase table below (longest phrase first), so the
# Bengali code stays untouched. Texts typed by an admin (custom welcome text, payment note ...) are
# shown exactly as the admin wrote them. To fix or add an English phrase just edit EN_TEXT.
import copy

EN_TEXT = {
    _WELCOME_V1: "This is a Python bot hosting service — you can run everything straight from Telegram. Upload your bot/script files, tap ▶️ Start — that's it! No need to know anything about servers or terminals.\n\n✨ Why choose us?\n\n✅ Hassle-free — upload a .py or a whole .zip, requirements.txt installs automatically\n✅ Online 24/7 — if it crashes you instantly get the log and a fix suggestion, auto-restart on paid plans\n✅ All in one place — File Manager, live logs, Package Manager, GitHub sync, backups\n✅ Secure — every project runs in its own folder and its own process\n✅ Affordable — free trial, low-priced plans, easy payment via bKash/Nagad\n✅ Refer friends and earn balance for free",
    DEFAULT_WELCOME: "This is a Python bot hosting service — you can run everything straight from Telegram. Upload your bot/script files, tap ▶️ Start — that's it! No need to know anything about servers or terminals.\n\n✨ Why choose us?\n\n⚡ Hassle-free — upload a .py or a whole .zip, requirements.txt installs automatically\n🌐 Online 24/7 — if it crashes you instantly get the log and a fix suggestion, auto-restart on paid plans\n🧰 All in one place — File Manager, live logs, Package Manager, GitHub sync, backups\n🔒 Secure — every project runs in its own folder and its own process\n💸 Affordable — free trial, low-priced plans, easy payment via bKash/Nagad\n🎁 Refer friends and earn balance for free",
    '🎛 কন্ট্রোল প্যানেল': '🎛 Control Panel',
    '📁 ফাইল ম্যানেজার': '📁 File Manager',
    '💎 প্ল্যান আপগ্রেড': '💎 Upgrade Plan',
    '👤 আমার অ্যাকাউন্ট': '👤 My Account',
    '🎁 রেফার ও আর্ন': '🎁 Refer & Earn',
    '📖 ডকুমেন্টেশন': '📖 Documentation',
    '🛰 সাপোর্ট হাব': '🛰 Support Hub',
    '🛠 অ্যাডমিন প্যানেল': '🛠 Admin Panel',
    '❌ মেয়াদ শেষ — প্ল্যান কিনুন': '❌ Expired — buy a plan',
    'নাম': 'Name',
    'মূল্য (৳)': 'Price (৳)',
    'মেয়াদ (দিন)': 'Duration (days)',
    'সর্বোচ্চ প্রজেক্ট': 'Max projects',
    'ডিস্ক (MB)': 'Disk (MB)',
    'চ্যানেল (কমা দিয়ে, @username)': 'Channels (comma-separated, @username)',
    'bKash নম্বর': 'bKash number',
    'Nagad নম্বর': 'Nagad number',
    'পেমেন্ট নোট (পেমেন্ট স্ক্রিনে দেখাবে)': 'Payment note (shown on the payment screen)',
    'রেফার রিওয়ার্ড (৳)': 'Referral reward (৳)',
    'সাপোর্ট @username': 'Support @username',
    'ওয়েলকাম টাইটেল': 'Welcome title',
    'ওয়েলকাম টেক্সট': 'Welcome text',
    'VPLink ফ্রি প্ল্যানের মেয়াদ (দিন)': 'VPLink free plan duration (days)',
    ' দিন\n<blockquote>🤖 ': ' days\n<blockquote>🤖 ',
    'টি বট চালাতে পারবেন\n💾 RAM: ': ' bot(s) allowed\n💾 RAM: ',
    '%\n💽 ডিস্ক: ': '%\n💽 Disk: ',
    '❗ <b>আপনি এখনো সব চ্যানেলে জয়েন করেননি!</b> জয়েন করে আবার ✅ চাপুন।\n\n': "❗ <b>You haven't joined all the channels yet!</b> Join them and tap ✅ again.\n\n",
    '<b>কীভাবে ভেরিফাই করবেন?</b>\n1️⃣ নিচের <b>📢 জয়েন করুন</b> বাটনে চাপ দিয়ে চ্যানেলে জয়েন করুন\n2️⃣ এখানে ফিরে এসে <b>✅ ভেরিফাই করুন</b> বাটনে চাপুন\n\n<blockquote>🎁 জয়েন করলেই ফ্রি প্ল্যান, রেফার রিওয়ার্ড ও সব আপডেট আনলক হবে!</blockquote>': '<b>How to verify?</b>\n1️⃣ Tap the <b>📢 Join</b> button below and join the channel\n2️⃣ Come back here and tap <b>✅ Verify</b>\n\n<blockquote>🎁 Joining unlocks the free plan, referral rewards and all updates!</blockquote>',
    'বট টোকেন ভুল বা বাতিল হয়েছে — টোকেন চেক করুন।': 'Bot token is invalid or revoked — check your token.',
    'একই টোকেনে আরেকটি বট চলছে — অন্য জায়গায় বন্ধ করুন।': 'Another bot is running with the same token — stop it elsewhere.',
    'কোডে সিনট্যাক্স/ইন্ডেন্টেশন ত্রুটি আছে।': 'The code has a syntax/indentation error.',
    'কোডে যে ফাইলটা খুঁজছে সেটা আপলোড করা হয়নি বা পাথ ভুল।': "The file the code is looking for wasn't uploaded, or the path is wrong.",
    'ফাইল/ফোল্ডারে পারমিশন নেই — প্রজেক্ট ফোল্ডারের ভেতরে লিখুন।': 'No permission for the file/folder — write inside the project folder.',
    'র\u200d্যাম শেষ — প্ল্যান আপগ্রেড করুন বা কোড অপ্টিমাইজ করুন।': 'Out of RAM — upgrade your plan or optimize your code.',
    'পোর্ট আগে থেকেই ব্যবহৃত হচ্ছে।': 'The port is already in use.',
    'সম্ভবত এনভায়রনমেন্ট ভেরিয়েবল সেট করা হয়নি — 🔐 এনভায়রনমেন্ট ভেরিয়েবল দেখুন।': 'The environment variables are probably not set — check 🔐 Environment Variables.',
    '\n\n⛔ বারবার ক্র্যাশ করায় অটো-রিস্টার্ট বন্ধ। কোড ঠিক করে নিজে চালু করুন।': '\n\n⛔ Auto-restart is off because of repeated crashes. Fix the code and start it manually.',
    '</b>\n<i>প্রিমিয়াম পাইথন বট হোস্টিং</i>\n': '</b>\n<i>Premium Python Bot Hosting</i>\n',
    '\n<blockquote>🎁 ফ্রি প্ল্যান: <b>': '\n<blockquote>🎁 Free plan: <b>',
    'টি বট</b>, প্রথম চালুর পর <b>': ' bot(s)</b>, valid for <b>',
    ' দিন</b>।\n💎 তারপর প্ল্যান কিনতে হবে — অথবা রেফার করে ব্যালেন্স আর্ন করুন!</blockquote>\n\n🚀 শুরু করতে 🎛 <b>কন্ট্রোল প্যানেল</b> থেকে নতুন প্রজেক্ট বানান।': " days</b> after the first start.\n💎 After that you'll need to buy a plan — or refer friends to earn balance!</blockquote>\n\n🚀 To get started, create a new project from 🎛 <b>Control Panel</b>.",
    '✅ <b>ভেরিফিকেশন সম্পন্ন!</b> আপনাকে স্বাগতম 🎉\n\n': '✅ <b>Verification complete!</b> Welcome 🎉\n\n',
    '🟢 অনলাইন': '🟢 Online',
    '⚙️ <b>প্রজেক্ট:</b> <code>': '⚙️ <b>Project:</b> <code>',
    '\n<blockquote>📡 স্ট্যাটাস: <b>': '\n<blockquote>📡 Status: <b>',
    '</b>\n💾 র\u200d্যাম: ': '</b>\n💾 RAM: ',
    '\n🧠 সিপিউ: ': '\n🧠 CPU: ',
    '\n⬆️ আপটাইম: ': '\n⬆️ Uptime: ',
    '\n📝 এন্ট্রি: ': '\n📝 Entry: ',
    '</blockquote>\n<blockquote>👑 প্ল্যান: <b>': '</blockquote>\n<blockquote>👑 Plan: <b>',
    '</b>\n⏳ বাকি সময়: ': '</b>\n⏳ Time left: ',
    '⏳ চালু হচ্ছে…': '⏳ Starting…',
    '🔁 রিস্টার্ট হচ্ছে…': '🔁 Restarting…',
    '💾 ব্যাকআপ তৈরি হচ্ছে…': '💾 Creating backup…',
    '⏳ ইন্সটল হচ্ছে…': '⏳ Installing…',
    '⏳ requirements.txt ইন্সটল হচ্ছে…': '⏳ Installing requirements.txt…',
    '(কিছু ইন্সটল করা নেই)': '(nothing installed)',
    'কোনো ভেরিয়েবল নেই': 'No variables',
    '✅ URL সেভ হয়েছে।': '✅ URL saved.',
    '⏳ সিঙ্ক হচ্ছে…': '⏳ Syncing…',
    '📁 <b>কোন প্রজেক্টের ফাইল দেখবেন?</b>': "📁 <b>Which project's files do you want to see?</b>",
    'এখন কোনো প্ল্যান চালু নেই।': 'No plan is active right now.',
    '⏹ বন্ধ করা হয়েছে': '⏹ Stopped',
    '✅ আপডেট হয়েছে।': '✅ Updated.',
    '➕ <b>নতুন প্ল্যান</b>\nএই ফরম্যাটে পাঠান:\n<code>id|নাম|দাম|দিন|প্রজেক্ট|ram_mb|cpu%|disk_mb</code>\nযেমন: <code>vip|💎 VIP|499|30|100|4096|300|10000</code>\n\nবাতিল: /cancel': '➕ <b>New plan</b>\nSend in this format:\n<code>id|name|price|days|projects|ram_mb|cpu%|disk_mb</code>\nExample: <code>vip|💎 VIP|499|30|100|4096|300|10000</code>\n\nCancel: /cancel',
    '✅ প্ল্যান যোগ হয়েছে।': '✅ Plan added.',
    '💰 পরিমাণ লিখুন (যোগ: <code>50</code>, কমানো: <code>-20</code>):\n\nবাতিল: /cancel': '💰 Enter the amount (add: <code>50</code>, subtract: <code>-20</code>):\n\nCancel: /cancel',
    '🎁 কোন প্ল্যান দেবেন?': '🎁 Which plan do you want to give?',
    'ট্রায়াল রিসেট হয়েছে': 'Trial reset',
    'প্ল্যান বাতিল': 'Plan cancelled',
    '✉️ মেসেজ লিখুন (ইউজারকে পাঠানো হবে):\n\nবাতিল: /cancel': '✉️ Write the message (it will be sent to the user):\n\nCancel: /cancel',
    '⏳ ব্রডকাস্ট শুরু হয়েছে…': '⏳ Broadcast started…',
    '✅ সেভ হয়েছে।': '✅ Saved.',
    '♻️ ডিফল্ট ওয়েলকাম সেট হয়েছে': '♻️ Default welcome restored',
    '👮 নতুন অ্যাডমিনের Telegram ID পাঠান (সে আগে বটে /start দিয়ে থাকতে হবে):\n\nবাতিল: /cancel': "👮 Send the new admin's Telegram ID (they must have sent /start to the bot first):\n\nCancel: /cancel",
    '🛡 আপনাকে অ্যাডমিন বানানো হয়েছে। অ্যাডমিন মেনু পেতে /start দিন।': '🛡 You have been made an admin. Send /start to get the admin menu.',
    '✅ অ্যাডমিন যোগ হয়েছে।': '✅ Admin added.',
    'পাথ অনুমোদিত নয়': 'Path not allowed',
    'ফোল্ডার পাওয়া যায়নি': 'Folder not found',
    'ফাইল পাওয়া যায়নি': 'File not found',
    'ফাইল অনেক বড়': 'File too large',
    'রুট ফোল্ডার ডিলিট করা যাবে না': 'The root folder cannot be deleted',
    'পাওয়া যায়নি': 'Not found',
    'আনজিপ সাইজ লিমিট ছাড়িয়ে গেছে': 'Unzip size limit exceeded',
    'জিপ ফাইল খালি': 'The zip file is empty',
    '\n♻️ অটো-রিস্টার্ট ✔</blockquote>': '\n♻️ Auto-restart ✔</blockquote>',
    ' দিন': ' days',
    'ট্রায়াল শুরু হয়নি (প্রথম চালুর পর ': 'Trial not started (',
    ' দিন)': ' days after the first start)',
    'ট্রায়াল: ': 'Trial: ',
    ' বাকি': ' left',
    "কোনো এন্ট্রি ফাইল (যেমন main.py) পাওয়া যায়নি। ফাইল আপলোড করুন অথবা '📝 এন্ট্রি ফাইল' থেকে বেছে নিন।": "No entry file (e.g. main.py) found. Upload a file or choose one from '📝 Entry File'.",
    'স্টার্ট বাতিল করা হয়েছে।': 'Start cancelled.',
    'মডিউল <code>': 'Module <code>',
    '</code> নেই — 📦 Package Manager থেকে <code>': '</code> is missing — in 📦 Package Manager, install <code>',
    '</code> ইন্সটল করুন।': '</code>.',
    'প্রজেক্ট ক্র্যাশ করেছে': 'Project crashed',
    '\n💡 <b>সমাধান:</b> ': '\n💡 <b>Fix:</b> ',
    '👋 হ্যালো, <b>': '👋 Hello, <b>',
    '🚫 আপনাকে ব্যান করা হয়েছে।': '🚫 You have been banned.',
    '✅ ভেরিফাই সফল!': '✅ Verified successfully!',
    "<b>1️⃣ প্রজেক্ট বানান</b>\n<blockquote>🎛 কন্ট্রোল প্যানেল → ➕ নতুন প্রজেক্ট</blockquote>\n<b>2️⃣ ফাইল আপলোড</b>\n<blockquote>.py ফাইল বা পুরো প্রজেক্টের .zip পাঠান (সর্বোচ্চ 20MB)। <code>requirements.txt</code> থাকলে চালুর সময় অটো ইন্সটল হবে।</blockquote>\n<b>3️⃣ টোকেন/সিক্রেট</b>\n<blockquote>🔐 এনভায়রনমেন্ট ভেরিয়েবলে সেট করুন, কোডে <code>os.getenv('BOT_TOKEN')</code> দিয়ে নিন।</blockquote>\n<b>4️⃣ চালু করুন</b>\n<blockquote>▶️ চালু করুন চাপুন। এরর হলে আমি সাথে সাথে লগ + সমাধান পাঠাব।</blockquote>\n<b>5️⃣ আরও টুল</b>\n<blockquote>📦 Package Manager — pip প্যাকেজ ইন্সটল\n🐙 গিটহাব সিঙ্ক\n💾 ব্যাকআপ — ZIP ডাউনলোড\n♻️ অটো-রিস্টার্ট (পেইড প্ল্যান)</blockquote>\n<b>⚠️ নিয়ম</b>\n<blockquote>লিমিটের বেশি RAM/CPU ব্যবহার করলে প্রজেক্ট বন্ধ হবে। অবৈধ/ক্ষতিকর কাজে ব্যবহার নিষিদ্ধ।</blockquote>": "<b>1️⃣ Create a project</b>\n<blockquote>🎛 Control Panel → ➕ New Project</blockquote>\n<b>2️⃣ Upload files</b>\n<blockquote>Send a .py file or the whole project as a .zip (max 20MB). If <code>requirements.txt</code> is present it installs automatically on start.</blockquote>\n<b>3️⃣ Tokens/secrets</b>\n<blockquote>🔐 Set them as environment variables and read them in code with <code>os.getenv('BOT_TOKEN')</code>.</blockquote>\n<b>4️⃣ Start it</b>\n<blockquote>Tap ▶️ Start. On an error I'll instantly send the log + a fix suggestion.</blockquote>\n<b>5️⃣ More tools</b>\n<blockquote>📦 Package Manager — install pip packages\n🐙 GitHub Sync\n💾 Backup — ZIP download\n♻️ Auto-restart (paid plans)</blockquote>\n<b>⚠️ Rules</b>\n<blockquote>Using more RAM/CPU than your limit will stop the project. Illegal/harmful use is prohibited.</blockquote>",
    '❌ প্রজেক্ট পাওয়া যায়নি': '❌ Project not found',
    '🟡 চালু হচ্ছে…': '🟡 Starting…',
    '<blockquote>২-৩০ অক্ষর • ইংরেজি/সংখ্যা/স্পেস/-/_</blockquote>\n\nবাতিল: /cancel': '<blockquote>2–30 characters • letters/numbers/spaces/-/_</blockquote>\n\nCancel: /cancel',
    '❌ নাম ঠিক নয়। ২-৩০ অক্ষর, শুধু ইংরেজি/সংখ্যা/স্পেস/-/_ ব্যবহার করুন।': '❌ Invalid name. Use 2–30 characters: only English letters, numbers, spaces, - and _.',
    '⛔ প্রজেক্ট লিমিট শেষ।': '⛔ Project limit reached.',
    '📤 এখন আপনার <code>.py</code> ফাইল বা পুরো প্রজেক্টের <code>.zip</code> পাঠান।\n<blockquote>requirements.txt থাকলে অটো ইন্সটল হবে। একাধিক ফাইল পাঠাতে পারেন।</blockquote>': '📤 Now send your <code>.py</code> file or the whole project as a <code>.zip</code>.\n<blockquote>requirements.txt installs automatically if present. You can send multiple files.</blockquote>',
    '⛔ ফ্রি ট্রায়াল/প্ল্যান শেষ। 💎 প্ল্যান কিনুন।': '⛔ Free trial/plan has ended. 💎 Buy a plan.',
    '⛔ প্ল্যানের লিমিটের বেশি প্রজেক্ট একসাথে চালানো যাবে না।': "⛔ You can't run more projects at once than your plan's limit.",
    '🎁 ফ্রি ট্রায়াল শুরু! শেষ হবে: <b>': '🎁 Free trial started! Ends: <b>',
    'ইতোমধ্যে চলছে': 'Already running',
    '⏳ চালু হচ্ছে — requirements ইন্সটল হতে সময় লাগে। একটু পরে 🔄 রিফ্রেশ দিন।': '⏳ Starting — installing requirements takes time. Tap 🔄 Refresh in a moment.',
    '♻️ অটো-রিস্টার্ট শুধু পেইড প্ল্যানে পাওয়া যায়। 💎 আপগ্রেড করুন।': '♻️ Auto-restart is available only on paid plans. 💎 Upgrade.',
    '📋 <b>লগ — ': '📋 <b>Log — ',
    'লগ খালি': 'Log is empty',
    '🗑 ডিলিট হয়েছে': '🗑 Deleted',
    'pip প্যাকেজ আপনার প্রজেক্টে আলাদাভাবে ইন্সটল হয়।': 'pip packages are installed separately for your project.',
    '📦 প্যাকেজের নাম লিখুন (স্পেস দিয়ে একাধিক)\nযেমন: <code>requests aiohttp==3.9.5 pyTelegramBotAPI</code>\n\nবাতিল: /cancel': '📦 Enter the package name(s) (separate multiple with spaces)\nExample: <code>requests aiohttp==3.9.5 pyTelegramBotAPI</code>\n\nCancel: /cancel',
    '❌ প্যাকেজ নাম ঠিক নয়। শুধু নাম/ভার্সন লিখুন (যেমন requests==2.31.0)।': '❌ Invalid package name. Enter only the name/version (e.g. requests==2.31.0).',
    '⛔ ডিস্ক কোটা শেষ। ফাইল মুছুন বা প্ল্যান আপগ্রেড করুন।': '⛔ Disk quota exceeded. Delete files or upgrade your plan.',
    '⏳ ইন্সটল হচ্ছে: <code>': '⏳ Installing: <code>',
    '❌ requirements.txt আপলোড করা নেই': '❌ requirements.txt is not uploaded',
    '⛔ ডিস্ক কোটা শেষ': '⛔ Disk quota exceeded',
    '📋 <b>ইন্সটল করা প্যাকেজ</b>\n<pre>': '📋 <b>Installed packages</b>\n<pre>',
    '🧹 লাইব্রেরি মুছে ফেলা হয়েছে': '🧹 Libraries removed',
    '🔐 <code>KEY=VALUE</code> ফরম্যাটে পাঠান\nযেমন: <code>BOT_TOKEN=123:ABC...</code>\n\n⚠️ মেসেজটি সেভ হলে আমি ডিলিট করে দেব।\nবাতিল: /cancel': '🔐 Send in <code>KEY=VALUE</code> format\nExample: <code>BOT_TOKEN=123:ABC...</code>\n\n⚠️ I will delete your message once it is saved.\nCancel: /cancel',
    '❌ ফরম্যাট: KEY=VALUE': '❌ Format: KEY=VALUE',
    '❌ KEY শুধু ইংরেজি অক্ষর/সংখ্যা/_ (সর্বোচ্চ ৩২), VALUE খালি হবে না।': '❌ KEY may contain only English letters/numbers/_ (max 32), and VALUE must not be empty.',
    '</code> সেভ হয়েছে। রিস্টার্ট দিলে কার্যকর হবে।': '</code> saved. Takes effect after a restart.',
    '🗑 মুছে ফেলা হয়েছে': '🗑 Removed',
    '🐙 <b>গিটহাব সিঙ্ক</b>\nরিপোর URL পাঠান:\n<code>https://github.com/user/repo</code>\nপ্রাইভেট রিপোর জন্য: <code>https://user:TOKEN@github.com/user/repo</code>\n\nবাতিল: /cancel': '🐙 <b>GitHub Sync</b>\nSend the repo URL:\n<code>https://github.com/user/repo</code>\nFor a private repo: <code>https://user:TOKEN@github.com/user/repo</code>\n\nCancel: /cancel',
    '🐙 <b>গিটহাব সিঙ্ক</b>\n🔗 <code>': '🐙 <b>GitHub Sync</b>\n🔗 <code>',
    '</code>\n\nসিঙ্ক করলে রিপোর লেটেস্ট ফাইল এই প্রজেক্টে আসবে (একই নামের ফাইল ওভাররাইট হবে)।': "</code>\n\nSyncing brings the repo's latest files into this project (files with the same name are overwritten).",
    '🐙 নতুন রিপো URL পাঠান:\n\nবাতিল: /cancel': '🐙 Send the new repo URL:\n\nCancel: /cancel',
    '❌ শুধু https://github.com / gitlab.com / bitbucket.org এর রিপো URL দিন।': '❌ Only repo URLs from https://github.com / gitlab.com / bitbucket.org are allowed.',
    '❌ কোনো .py ফাইল নেই। আগে ফাইল আপলোড করুন।': '❌ No .py file. Upload a file first.',
    '✅ এন্ট্রি ফাইল সেট হয়েছে': '✅ Entry file set',
    '❌ সেশন শেষ। আবার ফাইল ম্যানেজার খুলুন।': '❌ Session ended. Open the File Manager again.',
    '\n💽 ব্যবহৃত: ': '\n💽 Used: ',
    '📁 আগে একটি প্রজেক্ট বানান: 🎛 কন্ট্রোল প্যানেল → ➕ নতুন প্রজেক্ট': '📁 Create a project first: 🎛 Control Panel → ➕ New Project',
    'সেশন শেষ, রিফ্রেশ করুন': 'Session ended, refresh',
    'বাইনারি ফাইল — ডাউনলোড করুন': 'Binary file — download it',
    'ফাইল ৫০MB এর বেশি': 'File is larger than 50MB',
    '📁 নতুন ফোল্ডারের নাম লিখুন:\n\nবাতিল: /cancel': '📁 Enter the new folder name:\n\nCancel: /cancel',
    '❌ নাম ঠিক নয় (ইংরেজি/সংখ্যা/._-)।': '❌ Invalid name (letters/numbers/._-).',
    '✅ ফোল্ডার তৈরি হয়েছে': '✅ Folder created',
    'শুধু ১০০KB এর ছোট টেক্সট ফাইল এডিট করা যায়': 'Only text files up to 100KB can be edited',
    '</b>\nনতুন সম্পূর্ণ কনটেন্ট মেসেজে পাঠান (পুরনো কনটেন্ট রিপ্লেস হবে)।\nবা একটি ফাইল পাঠান যেটা এটাকে রিপ্লেস করবে।\n\nবাতিল: /cancel': '</b>\nSend the new full content in a message (the old content will be replaced).\nOr send a file that will replace it.\n\nCancel: /cancel',
    '✅ ফাইল আপডেট হয়েছে। রিস্টার্ট দিলে কার্যকর হবে।': '✅ File updated. Takes effect after a restart.',
    '📤 ফাইল (document) পাঠান। শেষ হলে ✅ আপলোড শেষ চাপুন।': '📤 Send files (as documents). Tap ✅ Upload Done when finished.',
    '⏳ <b>ফাইল আপলোড হচ্ছে...</b>\n📄 <code>': '⏳ <b>Uploading file...</b>\n📄 <code>',
    '</code>\n📦 সাইজ: ': '</code>\n📦 Size: ',
    '\nঅনুগ্রহ করে অপেক্ষা করুন।': '\nPlease wait.',
    '\n\nআরও পাঠান বা শেষ করুন 👇': '\n\nSend more or finish 👇',
    'প্ল্যান সমূহ': 'Plans',
    'আপনার প্রয়োজন অনুযায়ী সেরাটি বেছে নিন': 'Choose the best one for your needs',
    '<blockquote>👑 আপনার প্ল্যান: <b>': '<blockquote>👑 Your plan: <b>',
    '\n💰 ব্যালেন্স: <b>৳': '\n💰 Balance: <b>৳',
    'প্ল্যান পাওয়া যায়নি': 'Plan not found',
    '⛔ এই পেমেন্ট মাধ্যম এখন বন্ধ আছে। অন্যটি বেছে নিন।': '⛔ This payment method is currently off. Choose another.',
    '⏳ আপনার ৩টি পেমেন্ট এখনো যাচাই বাকি। অপেক্ষা করুন।': '⏳ You still have 3 payments awaiting verification. Please wait.',
    '❌ ব্যালেন্স কম!': '❌ Insufficient balance!',
    '❌ TrxID ঠিক নয় (৬-২০ অক্ষর/সংখ্যা)। আবার পাঠান।': '❌ Invalid TrxID (6–20 letters/numbers). Send it again.',
    '❌ এই TrxID আগেই জমা দেওয়া হয়েছে।': '❌ This TrxID has already been submitted.',
    '⏳ আগের পেমেন্ট যাচাই বাকি।': '⏳ Your previous payment is still being verified.',
    'অ্যাপ্রুভালের অপেক্ষায়': 'Awaiting approval',
    '</code>\n📦 প্ল্যান: ': '</code>\n📦 Plan: ',
    '\n💵 পরিমাণ: <b>৳': '\n💵 Amount: <b>৳',
    '</b>\n📲 মাধ্যম: ': '</b>\n📲 Method: ',
    ' অ্যাপে TrxID ও পরিমাণ মিলিয়ে নিন।': ' — check the TrxID and amount in the app.',
    '❌ অনুমতি নেই': '❌ Not allowed',
    'ইতোমধ্যে প্রসেস করা হয়েছে': 'Already processed',
    '✅ আপনার ': '✅ Your ',
    ' প্ল্যান আগে থেকেই সক্রিয় (': ' plan is already active (',
    ' বাকি)। মেয়াদ শেষ হলে আবার নিতে পারবেন।': ' left). You can get it again after it expires.',
    '⛔ এই অপশন এখন বন্ধ আছে।': '⛔ This option is currently off.',
    '⏳ একটু অপেক্ষা করে আবার চাপুন।': '⏳ Wait a moment and tap again.',
    '⚠️ লিংক বানানো যায়নি। একটু পরে আবার চেষ্টা করুন।': "⚠️ Couldn't create the link. Try again in a moment.",
    'লিংকটি ঠিক নয় বা অন্য একাউন্টের। নিজের একাউন্ট থেকে নতুন লিংক নিন।': 'This link is invalid or belongs to another account. Get a new link from your own account.',
    'এই লিংক আগেই ব্যবহার হয়েছে বা নতুন লিংক নেওয়ায় বাতিল হয়েছে।': 'This link was already used, or it was cancelled because you got a new link.',
    'লিংকের মেয়াদ (৩০ মিনিট) শেষ। নতুন লিংক নিন।': 'The link has expired (30 minutes). Get a new link.',
    'আপনি লিংকের ধাপগুলো সম্পূর্ণ করেননি। নতুন লিংক নিয়ে সব ধাপ শেষ করুন।': "You haven't completed the link's steps. Get a new link and complete all steps.",
    'প্ল্যানটি এখন আর পাওয়া যাচ্ছে না। অ্যাডমিনের সাথে যোগাযোগ করুন।': 'This plan is no longer available. Contact the admin.',
    'এই লিংক আগেই ব্যবহার হয়েছে।': 'This link was already used.',
    'অ্যাডমিন প্যানেল': 'Admin Panel',
    'পূর্ণ নিয়ন্ত্রণ আপনার হাতে': 'Full control in your hands',
    '<blockquote>👥 ইউজার: <b>': '<blockquote>👥 Users: <b>',
    '</b>  •  🆕 আজ: <b>': '</b>  •  🆕 Today: <b>',
    '</b>\n📦 প্রজেক্ট: <b>': '</b>\n📦 Projects: <b>',
    ')\n💳 পেন্ডিং: <b>': ')\n💳 Pending: <b>',
    '</b>  •  💰 আয়: <b>৳': '</b>  •  💰 Income: <b>৳',
    'প্ল্যান নেই': 'No plan',
    '📦 <b>প্ল্যান: ': '📦 <b>Plan: ',
    '✏️ নতুন <b>': '✏️ Enter new <b>',
    '</b> লিখুন:\n\nবাতিল: /cancel': '</b>:\n\nCancel: /cancel',
    '⚠️ প্ল্যান <b>': '⚠️ Delete plan <b>',
    '</b> ডিলিট করবেন? (এই প্ল্যানের ইউজাররা FREE-তে নেমে যাবে)': '</b>? (users on this plan will drop to FREE)',
    ')\n💰 ৳': ')\n💰 ৳',
    ' • 👥 রেফার ': ' • 👥 Referrals ',
    '\n📦 প্রজেক্ট: ': '\n📦 Projects: ',
    ' • 🚫 ব্যান: ': ' • 🚫 Banned: ',
    '❌ সংখ্যায় ID দিন।': '❌ Enter the ID as a number.',
    '❌ ইউজার পাওয়া যায়নি।': '❌ User not found.',
    'ইউজার পাওয়া যায়নি': 'User not found',
    '💰 আপনার ব্যালেন্সে ৳': '💰 Your balance has been adjusted by ৳',
    ' পরিবর্তন করা হয়েছে।': '.',
    '✅ হয়েছে।\n\n': '✅ Done.\n\n',
    '🎁 অ্যাডমিন আপনাকে <b>': '🎁 The admin gave you the <b>',
    '</b> প্ল্যান দিয়েছে!\n⏳ মেয়াদ: ': '</b> plan!\n⏳ Validity: ',
    '📢 এই মেসেজটি <b>': '📢 Send this message to <b>',
    '</b> জনকে পাঠাবেন?': '</b> users?',
    '❌ বাতিল।': '❌ Cancelled.',
    '\n<blockquote>📢 @username বা t.me লিংক দিন (একাধিক হলে কমা দিয়ে)। ভেরিফিকেশনের জন্য বটকে চ্যানেলে <b>Admin</b> বানাতে হবে।</blockquote>\n': '\n<blockquote>📢 Enter @username or t.me link (separate multiple with commas). For verification the bot must be made <b>Admin</b> in the channel.</blockquote>\n',
    '❌ ১ থেকে ৩৬৫ এর মধ্যে সংখ্যা দিন।': '❌ Enter a number between 1 and 365.',
    '👋 ওয়েলকাম মেসেজ': '👋 Welcome Message',
    '⚙️ সেটিংস': '⚙️ Settings',
    'প্ল্যান সিলেক্ট করুন 👇': 'Select a plan 👇',
    '❌ শুধু মেইন অ্যাডমিন': '❌ Main admins only',
    '❌ সঠিক ID দিন — ইউজারকে আগে বটে /start দিতে হবে।': '❌ Enter a valid ID — the user must send /start to the bot first.',
    '🗑 সরানো হয়েছে': '🗑 Removed',
    'ফাইল পাওয়া যায়নি: ': 'File not found: ',
    'এটা বৈধ জিপ ফাইল নয়': 'This is not a valid zip file',
    'চ্যানেল ভেরিফিকেশন কাজ করছে না': 'Channel verification is not working',
    'ইউজাররা ভেরিফাই করতে পারছে না': 'Users cannot verify',
    '</blockquote>\n\n<b>সমাধান:</b>\n1️⃣ বটকে ওই চ্যানেলে <b>Admin</b> বানান\n2️⃣ চ্যানেলের @username ঠিক আছে কিনা দেখুন (🛠 অ্যাডমিন → ⚙️ সেটিংস)': '</blockquote>\n\n<b>Solution:</b>\n1️⃣ Make the bot an <b>Admin</b> in that channel\n2️⃣ Check that the channel @username is correct (🛠 Admin → ⚙️ Settings)',
    '</b> — জয়েন করা আছে': '</b> — joined',
    '✅ ভেরিফাই করুন': '✅ Verify',
    'টাইমআউট': 'Timeout',
    'Timeout (15 মিনিট)': 'Timeout (15 minutes)',
    '⏳ আগের স্টার্ট এখনো চলছে (requirements ইন্সটল হতে সময় লাগে)। শেষ হলে নিজে থেকেই চালু হবে — একটু পরে 🔄 রিফ্রেশ দিন।': '⏳ The previous start is still running (installing requirements takes time). It will start on its own when done — tap 🔄 Refresh in a moment.',
    'এটি ইতোমধ্যে চালু আছে।': 'It is already running.',
    '</b> স্বাভাবিকভাবে শেষ হয়েছে (exit 0)।\nবট হলে সম্ভবত কোডে polling/loop নেই।': '</b> finished normally (exit 0).\nIf it is a bot, the code probably has no polling/loop.',
    '❌ আপনি এখনো চ্যানেলে জয়েন করেননি!': "❌ You haven't joined the channel yet!",
    '⚠️ এই মুহূর্তে ভেরিফাই করা যাচ্ছে না। অ্যাডমিনকে জানানো হয়েছে — একটু পরে আবার চেষ্টা করুন।': "⚠️ Verification isn't possible right now. The admin has been notified — try again in a moment.",
    'আমার অ্যাকাউন্ট': 'My Account',
    'আপনার প্রোফাইল ও প্ল্যানের সারাংশ': 'Summary of your profile and plan',
    '</code>\n📅 জয়েন: ': '</code>\n📅 Joined: ',
    '</b>\n⏳ বাকি: ': '</b>\n⏳ Left: ',
    '\n📦 প্রজেক্ট: <b>': '\n📦 Projects: <b>',
    '\n💾 RAM লিমিট: ': '\n💾 RAM limit: ',
    '%</blockquote>\n<blockquote>💰 ব্যালেন্স: <b>৳': '%</blockquote>\n<blockquote>💰 Balance: <b>৳',
    '</b>\n👥 রেফার: <b>': '</b>\n👥 Referrals: <b>',
    '</b> জন</blockquote>': '</b></blockquote>',
    'রেফার ও আর্ন': 'Refer & Earn',
    'বন্ধুদের ইনভাইট করুন, ব্যালেন্স জিতুন': 'Invite friends, win balance',
    'প্রতিটি সফল রেফারে পাবেন <b>৳': 'You get <b>৳',
    '</b>!\n<i>(রেফার করা ব্যক্তি চ্যানেল ভেরিফাই করলে ব্যালেন্স যোগ হবে)</i>\n\n🔗 <b>আপনার রেফার লিংক</b>\n<code>': '</b> for every successful referral!\n<i>(The balance is added when the referred person verifies the channel)</i>\n\n🔗 <b>Your referral link</b>\n<code>',
    '</code>\n\n<blockquote>👥 মোট রেফার: <b>': '</code>\n\n<blockquote>👥 Total referrals: <b>',
    '</b>\n💰 ব্যালেন্স: <b>৳': '</b>\n💰 Balance: <b>৳',
    '</b></blockquote>\n\n💎 ব্যালেন্স দিয়ে প্ল্যান কিনতে পারবেন।': '</b></blockquote>\n\n💎 You can buy a plan with your balance.',
    'ডকুমেন্টেশন': 'Documentation',
    'শুরু থেকে শেষ — ধাপে ধাপে গাইড': 'A step-by-step guide from start to finish',
    'সমস্যা হলে নিচের বাটনে যোগাযোগ করুন।\n<blockquote>🧾 পেমেন্ট সমস্যায় TrxID সহ জানাবেন।</blockquote>': 'If you have a problem, contact us with the button below.\n<blockquote>🧾 For payment problems, include the TrxID.</blockquote>',
    '🟠 ক্র্যাশড': '🟠 Crashed',
    '🔴 অফলাইন': '🔴 Offline',
    '🔄 রিফ্রেশ': '🔄 Refresh',
    '📋 লগ দেখুন': '📋 View Log',
    '🐙 গিটহাব সিঙ্ক': '🐙 GitHub Sync',
    '📝 এন্ট্রি ফাইল': '📝 Entry File',
    '📁 ফাইলস': '📁 Files',
    '💾 ব্যাকআপ': '💾 Backup',
    '🔐 এনভায়রনমেন্ট ভেরিয়েবল': '🔐 Environment Variables',
    '🗑 ডিলিট': '🗑 Delete',
    '◀️ পেছনে যান': '◀️ Go Back',
    '➕ নতুন প্রজেক্ট': '➕ New Project',
    'আপনার প্রজেক্ট বেছে নিন 👇': 'Choose your project 👇',
    'কোনো প্রজেক্ট নেই। ➕ নতুন প্রজেক্ট বানান।': 'No projects. Create one with ➕ New Project.',
    '⛔ আপনার প্ল্যানে সর্বোচ্চ ': '⛔ Your plan allows at most ',
    'টি প্রজেক্ট। প্ল্যান আপগ্রেড করুন।': ' project(s). Upgrade your plan.',
    'নতুন প্রজেক্টের নাম লিখুন': 'Enter the new project name',
    'একটি ছোট ও সহজ নাম দিন': 'Give it a short, simple name',
    'প্রজেক্ট তৈরি হয়েছে!': 'Project created!',
    '❌ <b>চালু করা যায়নি:</b>\n<pre>': '❌ <b>Could not start:</b>\n<pre>',
    '⚠️ ব্যাকআপ ৫০MB এর বেশি, টেলিগ্রামে পাঠানো যাচ্ছে না।': '⚠️ The backup is over 50MB and cannot be sent via Telegram.',
    '</b> সম্পূর্ণ ডিলিট করবেন? ফাইল, লগ, এনভায়রনমেন্ট সব মুছে যাবে!': '</b> — delete completely? All files, logs and environment variables will be erased!',
    '✅ ইন্সটল সফল': '✅ Install successful',
    '❌ ইন্সটল ব্যর্থ': '❌ Install failed',
    '➕ নতুন ভেরিয়েবল': '➕ New variable',
    '◀️ পেছনে': '◀️ Back',
    'এনভায়রনমেন্ট ভেরিয়েবল': 'Environment Variables',
    "</blockquote>\n\nকোডে: <code>os.getenv('KEY')</code>\nডিলিট করতে ভেরিয়েবলের বাটনে চাপুন।": "</blockquote>\n\nIn code: <code>os.getenv('KEY')</code>\nTap a variable's button to delete it.",
    'সর্বোচ্চ ৫০টি ভেরিয়েবল': 'Maximum 50 variables',
    '📝 <b>কোন ফাইলটি চালু হবে?</b>\nবর্তমান: <code>': '📝 <b>Which file should run?</b>\nCurrent: <code>',
    '⬆️ উপরে': '⬆️ Up',
    '📤 আপলোড': '📤 Upload',
    '➕ ফোল্ডার': '➕ Folder',
    '◀️ প্রজেক্ট': '◀️ Project',
    '\n(ফোল্ডার খালি)': '\n(folder is empty)',
    '👁 দেখুন': '👁 View',
    '📥 ডাউনলোড': '📥 Download',
    '✏️ এডিট': '✏️ Edit',
    '◀️ ফোল্ডারে ফিরুন': '◀️ Back to folder',
    '</b> ডিলিট করবেন?': '</b> — delete it?',
    '✅ আনজিপ হয়েছে (': '✅ Unzipped (',
    'টি ফাইল)': ' files)',
    '❌ টেক্সট বা ছোট ফাইল পাঠান।': '❌ Send text or a small file.',
    '📤 <b>আপলোড মোড</b>\n📍 <code>/': '📤 <b>Upload mode</b>\n📍 <code>/',
    '</code>\n\n.py / requirements.txt / যেকোনো ফাইল বা .zip পাঠান (সর্বোচ্চ ': '</code>\n\nSend .py / requirements.txt / any file or .zip (max ',
    'MB)। ZIP অটো আনজিপ হবে।': 'MB). ZIP is unzipped automatically.',
    '❌ ফাইল ': '❌ File is larger than ',
    'MB এর বেশি।': 'MB.',
    '❌ <b>ফাইল আপলোড ব্যর্থ হয়েছে</b>\n<code>': '❌ <b>File upload failed</b>\n<code>',
    'ক্রয় নিশ্চিত করুন': 'Confirm purchase',
    'শেষ ধাপ — পেমেন্ট মাধ্যম বেছে নিন': 'Last step — choose a payment method',
    '\n\nপেমেন্ট মাধ্যম বেছে নিন 👇': '\n\nChoose a payment method 👇',
    '💰 ব্যালেন্স থেকে <b>৳': '💰 Deduct <b>৳',
    '</b> কেটে <b>': '</b> from your balance to buy <b>',
    '</b> কিনবেন?': '</b>?',
    'নিরাপদ ম্যানুয়াল পেমেন্ট — ৩টি সহজ ধাপ': 'Secure manual payment — 3 easy steps',
    '<b>1️⃣ Send Money করুন</b>\n<blockquote>': '<b>1️⃣ Send Money</b>\n<blockquote>',
    ' অ্যাপ খুলে <b>Send Money</b> করুন\n📱 নম্বর: <code>': ' app — <b>Send Money</b>\n📱 Number: <code>',
    '</code>\n💵 পরিমাণ: <b>৳': '</code>\n💵 Amount: <b>৳',
    '</b></blockquote>\n<b>2️⃣ TrxID কপি করুন</b>\nসেন্ড করার পর <b>Transaction ID (TrxID)</b> কপি করুন।\n\n<b>3️⃣ TrxID পাঠান</b>\nএখানে TrxID পাঠান 👇 (অ্যাডমিন যাচাই করে অ্যাপ্রুভ করলে প্ল্যান চালু হবে)\n\n⚠️ ঠিক ৳': '</b></blockquote>\n<b>2️⃣ Copy the TrxID</b>\nAfter sending, copy the <b>Transaction ID (TrxID)</b>.\n\n<b>3️⃣ Send the TrxID</b>\nSend the TrxID here 👇 (the plan activates after the admin verifies and approves it)\n\n⚠️ Send exactly ৳',
    ' পাঠাবেন। ভুল/ভুয়া TrxID দিলে একাউন্ট ব্যান হতে পারে।': '. A wrong/fake TrxID may get your account banned.',
    '\nবাতিল: /cancel': '\nCancel: /cancel',
    'পেমেন্ট সফল — উপভোগ করুন': 'Payment successful — enjoy',
    '<blockquote>⏳ মেয়াদ: ': '<blockquote>⏳ Validity: ',
    '</blockquote>\n\n🎛 কন্ট্রোল প্যানেল থেকে প্রজেক্ট চালু করুন।': '</blockquote>\n\n🎛 Start your projects from the Control Panel.',
    'পেমেন্ট জমা হয়েছে!': 'Payment submitted!',
    'যাচাইয়ের অপেক্ষায়': 'Awaiting verification',
    '</code></blockquote>\nঅ্যাডমিন যাচাই করলেই প্ল্যান সক্রিয় হবে (সাধারণত ৫-৩০ মিনিট)। আমি আপনাকে জানিয়ে দেব।': '</code></blockquote>\nThe plan activates once the admin verifies (usually 5–30 minutes). I will notify you.',
    'নতুন পেমেন্ট #': 'New payment #',
    'পেমেন্ট অ্যাপ্রুভ হয়েছে!': 'Payment approved!',
    'ধন্যবাদ — আপনার প্ল্যান এখন সক্রিয়': 'Thank you — your plan is now active',
    '<blockquote>👑 প্ল্যান: <b>': '<blockquote>👑 Plan: <b>',
    '</b>\n⏳ মেয়াদ: ': '</b>\n⏳ Validity: ',
    'পেমেন্ট বাতিল হয়েছে': 'Payment cancelled',
    'যাচাই সম্পন্ন হয়নি': 'Verification was not completed',
    '</code></blockquote>\nভুল মনে হলে 🛰 সাপোর্ট হাবে যোগাযোগ করুন।': '</code></blockquote>\nIf you think this is a mistake, contact 🛰 Support Hub.',
    'ফ্রি প্ল্যান — লিংক ভেরিফাই': 'Free plan — link verification',
    'ভেরিফাই সম্পন্ন হলেই প্ল্যান সক্রিয় হবে': 'The plan activates as soon as verification is complete',
    '\n\n<b>কীভাবে নেবেন?</b>\n1️⃣ নিচের <b>🔗 ভেরিফাই লিংক</b> বাটনে চাপুন\n2️⃣ লিংকের পেজের ধাপগুলো শেষ করুন (পেজ বন্ধ না করে)\n3️⃣ শেষে লাস্ট পেজে <b>GETLINK</b> চাপুন — প্ল্যান সাথে সাথে চালু হয়ে যাবে\n\n<blockquote>⚠️ লিংক ৩০ মিনিট চলবে এবং শুধু আপনার একাউন্টে একবারই কাজ করবে। লিংকের ধাপ না করে সরাসরি এলে প্ল্যান পাবেন না।</blockquote>': '\n\n<b>How to get it?</b>\n1️⃣ Tap the <b>🔗 Verify Link</b> button below\n2️⃣ Complete the steps on the link page (without closing the page)\n3️⃣ On the last page, tap <b>GETLINK</b> — the plan activates immediately\n\n<blockquote>⚠️ The link lasts 30 minutes and works only once, only for your account. If you come directly without doing the link steps, you will not get the plan.</blockquote>',
    'ভেরিফিকেশন সফল': 'Verification successful',
    '📊 স্ট্যাটস': '📊 Stats',
    '🧾 পেমেন্ট হিস্টরি': '🧾 Payment History',
    '📦 প্ল্যান ম্যানেজ': '📦 Manage Plans',
    '👤 ইউজার ম্যানেজ': '👤 Manage Users',
    '👑 💎 প্রিমিয়াম ইউজার': '👑 💎 Premium Users',
    '📦 সব প্রজেক্ট/ফাইল': '📦 All Projects/Files',
    '📢 ব্রডকাস্ট': '📢 Broadcast',
    '🖥 রানিং প্রজেক্ট': '🖥 Running Projects',
    '👮 অ্যাডমিন ম্যানেজ': '👮 Manage Admins',
    '💾 DB ব্যাকআপ': '💾 DB Backup',
    '◀️ অ্যাডমিন': '◀️ Admin',
    '⛔ অ্যাডমিন আপনার প্রজেক্ট <b>': '⛔ An admin has stopped your project <b>',
    '</b> বন্ধ করেছে।': '</b>.',
    '❌ সঠিক সংখ্যা দিন।': '❌ Enter a valid number.',
    '❌ ফরম্যাট ভুল বা id আগে থেকেই আছে (id: a-z 0-9 _)।': '❌ Wrong format, or the id already exists (id: a-z 0-9 _).',
    '💰 ব্যালেন্স যোগ/কমান': '💰 Add/Subtract Balance',
    '🎁 প্ল্যান দিন': '🎁 Give Plan',
    '♻️ ট্রায়াল রিসেট': '♻️ Reset Trial',
    '❌ প্ল্যান বাতিল': '❌ Cancel Plan',
    '✉️ মেসেজ': '✉️ Message',
    '❌ সংখ্যা দিন।': '❌ Enter a number.',
    '📩 <b>অ্যাডমিন:</b>\n': '📩 <b>Admin:</b>\n',
    '✅ পাঠানো হয়েছে।': '✅ Sent.',
    '❌ পাঠানো যায়নি (ইউজার বট ব্লক করেছে?)': "❌ Couldn't send (did the user block the bot?)",
    '📢 ব্রডকাস্ট শেষ\n✅ সফল: ': '📢 Broadcast finished\n✅ Succeeded: ',
    '\n❌ ব্যর্থ: ': '\n❌ Failed: ',
    '❌ সর্বোচ্চ ': '❌ Maximum ',
    ' অক্ষর (আপনারটা ': ' characters (yours is ',
    ')। ছোট করে আবার পাঠান।': '). Shorten it and send again.',
    '❌ আগে .env-এ VPLINK_API_KEY দিন।': '❌ First put VPLINK_API_KEY in .env.',
    '❌ আগে 📦 VPLink প্ল্যান বেছে নিন।': '❌ First choose a 📦 VPLink plan.',
    '◀️ সেটিংস': '◀️ Settings',
    'VPLink প্ল্যান বেছে নিন': 'Choose VPLink plan',
    'ভেরিফাই করলে ইউজার এই প্ল্যান পাবে': 'Users get this plan when they verify',
    '💎 প্ল্যান কিনুন': '💎 Buy Plan',
    'আপনার পেইড প্ল্যানের মেয়াদ শেষ হয়েছে। চালু রাখতে প্ল্যান রিনিউ করুন।': 'Your paid plan has expired. Renew your plan to keep it running.',
    'বট চালু রাখতে প্ল্যান কিনুন।': 'Buy a plan to keep your bots running.',
    '📢 জয়েন করুন ': '📢 Join ',
    '👇 মেনু থেকে একটি অপশন বেছে নিন।': '👇 Choose an option from the menu.',
    '📤 ফাইল আপলোড করতে: 📁 <b>ফাইল ম্যানেজার</b> → প্রজেক্ট → 📤 আপলোড।': '📤 To upload a file: 📁 <b>File Manager</b> → project → 📤 Upload.',
    '📋 লগ': '📋 Log',
    '🎛 প্যানেল': '🎛 Panel',
    'সার্ভারে git ইন্সটল নেই': 'git is not installed on the server',
    '</b>: requirements.txt ইন্সটল হচ্ছে… ১-২ মিনিট লাগতে পারে, শেষ হলে অটো চালু হবে।': '</b>: installing requirements.txt… may take 1–2 minutes; it will start automatically when done.',
    '(লগ খালি)': '(log is empty)',
    '\n\n♻️ ৫ সেকেন্ড পর অটো-রিস্টার্ট হবে…': '\n\n♻️ Auto-restart in 5 seconds…',
    '⏰ আপনার প্ল্যান/ট্রায়াল শেষ, তাই <b>': '⏰ Your plan/trial has ended, so <b>',
    '</b> বন্ধ করা হয়েছে।\n💎 প্ল্যান আপগ্রেড করে আবার চালু করুন।': '</b> was stopped.\n💎 Upgrade your plan and start it again.',
    '</b> র\u200d্যাম লিমিট (': '</b> exceeded its RAM limit of ',
    ' MB) ছাড়িয়েছে (': ' MB (using ',
    ' MB), তাই বন্ধ করা হয়েছে।\nকোড অপ্টিমাইজ করুন বা প্ল্যান আপগ্রেড করুন।': ' MB), so it was stopped.\nOptimize your code or upgrade your plan.',
    '✅ ঠিক আছে। মেনু থেকে বেছে নিন।': '✅ OK. Choose from the menu.',
    '🎁 রেফার': '🎁 Refer',
    '📤 শেয়ার করুন': '📤 Share',
    '📢 অফিসিয়াল চ্যানেল': '📢 Official Channel',
    '💬 সাপোর্টে মেসেজ': '💬 Message Support',
    'সাপোর্ট হাব': 'Support Hub',
    'আমরা আছি আপনার পাশে': "We're by your side",
    '\n💬 সাপোর্ট: <b>': '\n💬 Support: <b>',
    'সেট করা হয়নি': 'Not set',
    '🔁 রিস্টার্ট': '🔁 Restart',
    '⏹ বন্ধ করুন': '⏹ Stop',
    '♻️ অটো-রিস্টার্ট: ': '♻️ Auto-restart: ',
    'কন্ট্রোল প্যানেল': 'Control Panel',
    'আপনার সব প্রজেক্ট এক জায়গায়': 'All your projects in one place',
    '✅ <b>প্রজেক্ট চালু হয়েছে!</b>\n🆔 PID: <code>': '✅ <b>Project started!</b>\n🆔 PID: <code>',
    '📥 ফুল লগ': '📥 Full Log',
    '❌ ব্যাকআপ ব্যর্থ: ': '❌ Backup failed: ',
    '➕ প্যাকেজ ইন্সটল': '➕ Install Package',
    '📋 ইন্সটল করা': '📋 Installed',
    '📥 requirements.txt ইন্সটল': '📥 Install requirements.txt',
    '🧹 সব লাইব্রেরি মুছুন': '🧹 Remove All Libraries',
    '🔄 এখনই সিঙ্ক': '🔄 Sync Now',
    '✏️ URL পরিবর্তন': '✏️ Change URL',
    '\n📝 এন্ট্রি ফাইল সেট: <code>': '\n📝 Entry file set: <code>',
    'ফাইল ম্যানেজার': 'File Manager',
    '📝 এন্ট্রি ফাইল করুন': '📝 Make Entry File',
    '📦 আনজিপ': '📦 Unzip',
    ' (প্রথম অংশ)': ' (first part)',
    '</b> আনজিপ হয়েছে (': '</b> unzipped (',
    '</b> সেভ হয়েছে (': '</b> saved (',
    '\n📝 এন্ট্রি ফাইল অটো-সেট: <code>': '\n📝 Entry file auto-set: <code>',
    '❌ ব্যালেন্স কম! দরকার ৳': '❌ Insufficient balance! Needed ৳',
    ', আছে ৳': ', you have ৳',
    '।\nরেফার করে আর্ন করুন।': '.\nEarn by referring.',
    ' পেমেন্ট': ' Payment',
    ' সক্রিয় হয়েছে!': ' activated!',
    '🔗 ভেরিফাই লিংক': '🔗 Verify Link',
    'ভেরিফিকেশন হয়নি': 'Not verified',
    'প্ল্যান সক্রিয় করা যায়নি': 'The plan could not be activated',
    '💳 পেন্ডিং পেমেন্ট': '💳 Pending Payments',
    ' (টগল)': ' (toggle)',
    '♻️ অটো-রিস্টার্ট ': '♻️ Auto-restart ',
    '✅ হ্যাঁ': '✅ Yes',
    '❌ না': '❌ No',
    'হ্যাঁ': 'Yes',
    'না': 'No',
    '💬 চ্যাট করুন @': '💬 Chat @',
    ' (username নেই)': ' (no username)',
    '🚫 আনব্যান': '🚫 Unban',
    '🚫 ব্যান': '🚫 Ban',
    'দিন)': 'days)',
    'সেটিং পরিবর্তন': 'Change setting',
    'বর্তমান: <code>': 'Current: <code>',
    '\nনতুন মান পাঠান (খালি/ডিফল্ট করতে <code>-</code>):\n\nবাতিল: /cancel': '\nSend the new value (<code>-</code> to clear/use the default):\n\nCancel: /cancel',
    'প্ল্যান শীঘ্রই শেষ হবে!': 'Plan expiring soon!',
    'রিনিউ করে প্রজেক্ট চালু রাখুন': 'Renew to keep your projects running',
    '</blockquote>\n\nপ্রজেক্ট চালু রাখতে এখনই রিনিউ করুন।': '</blockquote>\n\nRenew now to keep your projects running.',
    'প্ল্যানের মেয়াদ শেষ': 'Plan expired',
    'এখন FREE প্ল্যান প্রযোজ্য': 'The FREE plan now applies',
    'ফ্রি ট্রায়াল শীঘ্রই শেষ!': 'Free trial ending soon!',
    '২৪ ঘণ্টার মধ্যে মেয়াদ শেষ হবে': 'Expires within 24 hours',
    'বট ব্যবহার শুরু করতে আগে আমাদের অফিসিয়াল চ্যানেলে জয়েন করুন।\n\n': 'To start using the bot, first join our official channel.\n\n',
    '🚫 আপনাকে ব্যান করা হয়েছে': '🚫 You have been banned',
    '⚠️ একটি ত্রুটি হয়েছে, আবার চেষ্টা করুন।': '⚠️ An error occurred, please try again.',
    '❌ requirements.txt ইন্সটল ব্যর্থ:\n': '❌ requirements.txt install failed:\n',
    '</b> দীর্ঘ সময় CPU লিমিট (': '</b> exceeded the CPU limit of ',
    '%) ছাড়িয়েছে (': '% for a long time (using ',
    '%), তাই বন্ধ করা হয়েছে।': '%), so it was stopped.',
    'নতুন রেফার!': 'New referral!',
    'আপনার লিংকে একজন জয়েন করেছে': 'Someone joined with your link',
    '<blockquote>💰 <b>+৳': '<blockquote>💰 <b>+৳',
    '</b> ব্যালেন্সে যোগ হয়েছে।</blockquote>': '</b> added to your balance.</blockquote>',
    '&text=ফ্রি বট হোস্টিং!': '&text=Free bot hosting!',
    '⏳ চালু হচ্ছে… রিফ্রেশ': '⏳ Starting… Refresh',
    '⏹ বাতিল': '⏹ Cancel',
    '▶️ চালু করুন': '▶️ Start',
    '✅ আপলোড শেষ': '✅ Upload Done',
    '</b> ব্যাকআপ • ': '</b> backup • ',
    '\nরিস্টোর করতে: ফাইল ম্যানেজারে এই ZIP আপলোড করুন।': '\nTo restore: upload this ZIP in the File Manager.',
    '✅ হ্যাঁ, ডিলিট': '✅ Yes, delete',
    '🔐 ভেরিয়েবল তালিকা': '🔐 Variable List',
    '✅ সিঙ্ক সম্পন্ন!': '✅ Sync complete!',
    '❌ সিঙ্ক ব্যর্থ:': '❌ Sync failed:',
    '🎁 ফ্রিতে নিন — ': '🎁 Get it free — ',
    '💰 ব্যালেন্স (৳': '💰 Balance (৳',
    '✅ হ্যাঁ, কিনুন': '✅ Yes, buy',
    '💳 পেন্ডিং পেমেন্ট (': '💳 Pending Payments (',
    'স্ট্যাটস': 'Stats',
    'রিয়েল-টাইম ওভারভিউ': 'Real-time overview',
    '</b> (ভেরিফাইড ': '</b> (verified ',
    ', ব্যান ': ', banned ',
    ')\n🆕 গত ২৪ ঘণ্টা: <b>': ')\n🆕 Last 24 hours: <b>',
    '</b>\n💎 পেইড ইউজার: <b>': '</b>\n💎 Paid users: <b>',
    '</b></blockquote>\n<blockquote>📦 প্রজেক্ট: <b>': '</b></blockquote>\n<blockquote>📦 Projects: <b>',
    ' চলছে)\n💳 পেন্ডিং পেমেন্ট: <b>': ' running)\n💳 Pending payments: <b>',
    '</b>\n💰 মোট আয়: <b>৳': '</b>\n💰 Total income: <b>৳',
    '</b></blockquote>\n🖥 <b>সার্ভার</b>\n<blockquote>🧠 CPU: ': '</b></blockquote>\n🖥 <b>Server</b>\n<blockquote>🧠 CPU: ',
    '\n💽 ডিস্ক: ': '\n💽 Disk: ',
    '📦 হোস্টিং RAM: আলাদা লিমিট পাওয়া যায়নি (উপরের RAM-ই পুরো সার্ভারের)': '📦 Hosting RAM: no separate limit found (the RAM above is the whole server)',
    '📦 হোস্টিং RAM: ': '📦 Hosting RAM: ',
    '✅ কোনো পেন্ডিং পেমেন্ট নেই।': '✅ No pending payments.',
    '💳 পেন্ডিং: ': '💳 Pending: ',
    'টি (নিচে পাঠালাম)': ' (sent below)',
    "📦 <b>প্ল্যান ম্যানেজ</b>\n(FREE প্ল্যানের 'মেয়াদ' = ফ্রি ট্রায়াল দিন)": "📦 <b>Manage Plans</b>\n(the FREE plan's 'validity' = free trial days)",
    '📦 প্ল্যান': '📦 Plan',
    '✅ পাঠান': '✅ Send',
    '❌ বাতিল': '❌ Cancel',
    '🎁 নতুন লিংক নিন': '🎁 Get New Link',
    '🟢 চালু': '🟢 On',
    '⚪ বন্ধ': '⚪ Off',
    'চ্যানেল ভেরিফিকেশন': 'Channel Verification',
    'বট ব্যবহারের আগে একটি ছোট্ট ধাপ': 'One small step before using the bot',
    '➕ নতুন প্ল্যান': '➕ New Plan',
    '👑 <b>প্রিমিয়াম ইউজার</b> — ': '👑 <b>Premium users</b> — ',
    ' জন (সর্বোচ্চ ৫০ জন)': ' (max 50)',
    '📦 <b>সব ইউজারের প্রজেক্ট</b>\nপ্রজেক্টে চাপলে তার ফাইল ম্যানেজার খুলবে।': "📦 <b>All users' projects</b>\nTap a project to open its file manager.",
    '🟢 ➕ ইউজার খুঁজুন': '🟢 ➕ Find User',
    '👤 ইউজারের Telegram ID পাঠান:\n\nবাতিল: /cancel': "👤 Send the user's Telegram ID:\n\nCancel: /cancel",
    '💾 অটো ডাটাবেস ব্যাকআপ (দৈনিক)': '💾 Automatic database backup (daily)',
    '📢 <b>ব্রডকাস্ট</b>\nযে মেসেজ (টেক্সট/ছবি/ভিডিও/ফাইল) সবাইকে পাঠাতে চান সেটা এখন পাঠান:\n\nবাতিল: /cancel': '📢 <b>Broadcast</b>\nSend the message (text/photo/video/file) you want to send to everyone now:\n\nCancel: /cancel',
    '🖥 <b>রানিং প্রজেক্ট (': '🖥 <b>Running projects (',
    ')</b>\nবাটনে চাপলে বন্ধ হবে।': ')</b>\nTap a button to stop it.',
    '🧾 <b>পেমেন্ট হিস্টরি (শেষ ১৫টি)</b>\n\n': '🧾 <b>Payment History (last 15)</b>\n\n',
    '📦 VPLink প্ল্যান: ': '📦 VPLink plan: ',
    'পরিবর্তন করতে বাটনে চাপুন।\n<blockquote>💗/🟠 বাটনে চাপলে সেই পেমেন্ট মাধ্যম চালু/বন্ধ হবে।\n📢 চ্যানেল ভেরিফিকেশন: বটকে চ্যানেলে Admin বানাতে হবে।\n': 'Tap a button to change.\n<blockquote>💗/🟠 Tapping the button turns that payment method on/off.\n📢 Channel verification: make the bot an Admin in the channel.\n',
    '🎁 VPLink: চালু করতে <code>.env</code>-এ VPLINK_API_KEY দিন, প্ল্যান বেছে ON করুন।': '🎁 VPLink: to enable, put VPLINK_API_KEY in <code>.env</code> and choose a plan, then turn it ON.',
    '🎁 VPLink: ইউজার ভেরিফাই করলে সিলেক্ট করা প্ল্যান পাবে।': '🎁 VPLink: when users verify, they get the selected plan.',
    'কোনো পেমেন্ট নেই।': 'No payments.',
    '👋 <b>ওয়েলকাম মেসেজ</b> — ইউজার নতুন এসে এটাই দেখবে:\n━━━━━━━━━━━━━━\n': '👋 <b>Welcome Message</b> — new users will see this:\n━━━━━━━━━━━━━━\n',
    'সেটিংস': 'Settings',
    'বট ও পেমেন্ট কনফিগারেশন': 'Bot & payment configuration',
    '💳 পেন্ডিং দেখুন': '💳 View Pending',
    '❌ শুধু মেইন অ্যাডমিন (.env-এর ADMIN_IDS) অ্যাডমিন ম্যানেজ করতে পারবে': '❌ Only the main admin (ADMIN_IDS in .env) can manage admins',
    '\n\nযোগ করা অ্যাডমিন পেমেন্ট অ্যাপ্রুভ/ইউজার/প্ল্যান/সেটিংস ম্যানেজ করতে পারবে। DB ব্যাকআপ ও অ্যাডমিন ম্যানেজ শুধু মেইন অ্যাডমিন করতে পারে।': '\n\nAdded admins can manage payment approvals/users/plans/settings. DB backup and admin management can only be done by the main admin.',
    '✏️ টাইটেল বদলান': '✏️ Change Title',
    '✏️ টেক্সট বদলান': '✏️ Change Text',
    '♻️ ডিফল্টে ফেরান': '♻️ Restore Default',
    '➕ অ্যাডমিন যোগ': '➕ Add Admin',
    '❌ শুধু মেইন অ্যাডমিন (.env-এর ADMIN_IDS) ডাটাবেস ব্যাকআপ নিতে পারবে': '❌ Only the main admin (ADMIN_IDS in .env) can take a database backup',
    '🗑 সরান: ': '🗑 Remove: ',
    '\n🛡 যোগ করা অ্যাডমিন: ': '\n🛡 Added admins: ',
    'কেউ নেই': 'None',
    '👮 <b>অ্যাডমিন ম্যানেজ</b>\n\n👑 মেইন (.env): ': '👮 <b>Manage Admins</b>\n\n👑 Main (.env): ',
    '💾 ডাটাবেস ব্যাকআপ': '💾 Database Backup',
    '🌐 ভাষা / Language': '🌐 Language',
    'ডাটা সাইজ লিমিট ছাড়িয়ে গেছে': 'Data size limit exceeded',
    '</b>: ব্যাকআপ থেকে ': '</b>: restored ',
    'টি ডাটা ফাইল ফিরিয়ে আনা হয়েছে।': ' data file(s) from your backup.',
    '🗄 ডাটা ব্যাকআপ': '🗄 Data Backup',
    '🕒 সর্বশেষ ব্যাকআপ: ': '🕒 Last backup: ',
    '\n📦 সাইজ: ': '\n📦 Size: ',
    '  •  🗂 কপি: ': '  •  🗂 Copies: ',
    '🕒 এখনো কোনো ডাটা ব্যাকআপ নেই': '🕒 No data backup yet',
    'ডাটা ব্যাকআপ': 'Data Backup',
    '<blockquote>📂 ডাটা ফোল্ডার: <code>data/</code>\n🔑 কোডে পাথ: <code>PROJECT_DATA_DIR</code> (env)</blockquote>\n': '<blockquote>📂 Data folder: <code>data/</code>\n🔑 Path in code: <code>PROJECT_DATA_DIR</code> (env)</blockquote>\n',
    'কোডের ডাটা (db / json / csv ...) <code>data/</code> ফোল্ডারে রাখুন — অটো ব্যাকআপ হবে। ডাটা হারিয়ে গেলেও প্রজেক্ট চালু করলেই সব ফিরে আসবে।\n<blockquote>ℹ️ ফোল্ডারের বাইরের <code>.db .sqlite .json .csv .pkl</code> ফাইলও ব্যাকআপে ধরা হয়। চালু অবস্থায় প্রতি ': "Keep your code's data (db / json / csv ...) in the <code>data/</code> folder — it is backed up automatically. Even if data is lost, just start the project and everything comes back.\n<blockquote>ℹ️ <code>.db .sqlite .json .csv .pkl</code> files outside the folder are backed up too. Automatic backup runs every ",
    ' মিনিটে এবং বন্ধ হলে অটো ব্যাকআপ হয়।</blockquote>': ' minutes while running, and whenever the project stops.</blockquote>',
    '💾 এখনই ব্যাকআপ': '💾 Backup Now',
    '📥 ডাটা ডাউনলোড': '📥 Download Data',
    '📤 ডাটা আপলোড / রিস্টোর': '📤 Upload / Restore Data',
    '♻️ সর্বশেষ ব্যাকআপ থেকে রিস্টোর': '♻️ Restore from Latest Backup',
    '🗑 ব্যাকআপ মুছুন': '🗑 Delete Backups',
    '✅ ডাটা ব্যাকআপ হয়েছে (': '✅ Data backed up (',
    'টি ফাইল, ': ' files, ',
    'ℹ️ কোনো পরিবর্তন নেই — আগের ব্যাকআপই সর্বশেষ।': 'ℹ️ No changes — the previous backup is still the latest.',
    'ℹ️ ব্যাকআপ করার মতো ডাটা ফাইল পাওয়া যায়নি। ডাটা <code>data/</code> ফোল্ডারে রাখুন।': 'ℹ️ No data files found to back up. Keep your data in the <code>data/</code> folder.',
    '❌ ডাটা ব্যাকআপ ব্যর্থ: ': '❌ Data backup failed: ',
    '⚠️ ডাটা ব্যাকআপ ৫০MB এর বেশি, টেলিগ্রামে পাঠানো যাচ্ছে না।': '⚠️ The data backup is over 50MB and cannot be sent via Telegram.',
    '</b> ডাটা ব্যাকআপ • ': '</b> data backup • ',
    '\nরিস্টোর করতে: 🗄 ডাটা ব্যাকআপ → 📤 ডাটা আপলোড / রিস্টোর।': '\nTo restore: 🗄 Data Backup → 📤 Upload / Restore Data.',
    'ডাটা আপলোড': 'Upload Data',
    'আপনার ডাটা ফাইল (<code>.zip</code>, <code>.db</code>, <code>.json</code>, <code>.csv</code> ...) ডকুমেন্ট হিসেবে পাঠান।\n<blockquote>ZIP হলে ভেতরের <code>data/</code> ফোল্ডার ও ডাটা ফাইলগুলো রিস্টোর হবে। একক ফাইল হলে প্রজেক্টে সেভ হবে।</blockquote>\n\nবাতিল: /cancel': 'Send your data file (<code>.zip</code>, <code>.db</code>, <code>.json</code>, <code>.csv</code> ...) as a document.\n<blockquote>For a ZIP, the <code>data/</code> folder and data files inside are restored. A single file is saved into the project.</blockquote>\n\nCancel: /cancel',
    '❌ ডাটা ফাইলটি ডকুমেন্ট (ফাইল) হিসেবে পাঠান।': '❌ Send the data file as a document (file).',
    'ℹ️ জিপে কোনো ডাটা ফাইল (<code>data/</code> ফোল্ডার বা .db/.json/.csv ...) পাওয়া যায়নি।': 'ℹ️ No data files (<code>data/</code> folder or .db/.json/.csv ...) found in the zip.',
    '✅ ডাটা রিস্টোর হয়েছে (': '✅ Data restored (',
    'টি ফাইল)।': ' files).',
    '</code> সেভ হয়েছে (': '</code> saved (',
    '\n🔁 প্রজেক্ট রিস্টার্ট করলে নতুন ডাটা কাজ করবে।': '\n🔁 Restart the project for the new data to take effect.',
    '❌ এখনো কোনো ডাটা ব্যাকআপ নেই।': '❌ There is no data backup yet.',
    '⚠️ সর্বশেষ ব্যাকআপ থেকে ডাটা রিস্টোর করবেন? বর্তমান ডাটা ফাইলগুলো ওভাররাইট হবে।': '⚠️ Restore data from the latest backup? Your current data files will be overwritten.',
    '✅ হ্যাঁ, রিস্টোর': '✅ Yes, restore',
    '♻️ রিস্টোর হচ্ছে…': '♻️ Restoring…',
    '♻️ ডাটা রিস্টোর হয়েছে (': '♻️ Data restored (',
    '⚠️ এই প্রজেক্টের সব ডাটা ব্যাকআপ কপি মুছে ফেলবেন? (প্রজেক্টের বর্তমান ফাইল মুছবে না)': "⚠️ Delete all data backup copies of this project? (The project's current files will not be deleted)",
    '✅ হ্যাঁ, মুছুন': '✅ Yes, delete',
    '🗑 ব্যাকআপ মুছে ফেলা হয়েছে': '🗑 Backups deleted',
    '🎬 ভিডিও সেট করুন': '🎬 Set Video',
    '🗑 ভিডিও সরান': '🗑 Remove Video',
    '\n\n🎬 ভিডিও: ': '\n\n🎬 Video: ',
    '✅ সেট করা আছে': '✅ Set',
    '❌ সেট করা নেই': '❌ Not set',
    '👁 প্রিভিউ': '👁 Preview',
    '🎬 ওয়েলকাম ভিডিও পাঠান (ভিডিও বা GIF), অথবা ভিডিওর লিংক দিন।\n<blockquote>ভিডিও সরাসরি ভিডিও হিসেবে পাঠান (ফাইল হিসেবে নয়)। এটি ওয়েলকাম মেসেজের সাথে দেখানো হবে।</blockquote>\n\nবাতিল: /cancel': '🎬 Send the welcome video (video or GIF), or give a video link.\n<blockquote>Send it directly as a video (not as a file). It is shown together with the welcome message.</blockquote>\n\nCancel: /cancel',
    '❌ ভিডিও, GIF অথবা ভিডিওর লিংক পাঠান।': '❌ Send a video, GIF or a video link.',
    '✅ ওয়েলকাম ভিডিও সেট হয়েছে।': '✅ Welcome video set.',
    '🗑 ভিডিও সরানো হয়েছে': '🗑 Video removed',
    '⚠️ ভিডিও পাঠানো যায়নি — নতুন করে সেট করুন।': '⚠️ The video could not be sent — set it again.',
    '✨ মেনু থেকে বেছে নিন': '✨ Choose from the menu',
}

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_BN_ANY = re.compile("[\u0985-\u09B9\u09BC-\u09CE\u09DC-\u09E3\u09E6-\u09EF]")
_BN_CHAR = "\u0980-\u09E5\u09F0-\u09FF"      # Bengali block without the digits
_BN_LETTER = re.compile("[\u0985-\u09B9\u09BC-\u09CE\u09DC-\u09E3]")


def _tr_pattern():
    parts = []
    for k in sorted(EN_TEXT, key=len, reverse=True):
        p = re.escape(k)
        if _BN_LETTER.match(k[0]):
            p = f"(?<![{_BN_CHAR}])" + p
        if _BN_LETTER.match(k[-1]):
            p += f"(?![{_BN_CHAR}])"
        parts.append(p)
    return re.compile("|".join(parts))


_TR_RE = _tr_pattern()


@functools.lru_cache(maxsize=4096)
def _tr_en(text):
    if not _BN_ANY.search(text):
        return text
    return _TR_RE.sub(lambda m: EN_TEXT[m.group(0)], text).translate(_BN_DIGITS)


def tr(text, lang="en"):
    """Translate an outgoing Bengali text to `lang` ('bn' = unchanged)."""
    if lang != "en" or not isinstance(text, str) or not text:
        return text
    return _tr_en(text)


def tr_markup(mk, lang):
    """Translated copy of an inline / reply keyboard (button text and url)."""
    if lang != "en" or mk is None:
        return mk
    try:
        new = copy.deepcopy(mk)
        for row in getattr(new, "keyboard", None) or []:
            for b in row:
                for key in ("text", "url"):
                    if isinstance(b, dict):
                        if isinstance(b.get(key), str):
                            b[key] = tr(b[key], lang)
                    elif isinstance(getattr(b, key, None), str):
                        setattr(b, key, tr(getattr(b, key), lang))
        ph = getattr(new, "input_field_placeholder", None)
        if isinstance(ph, str):
            new.input_field_placeholder = tr(ph, lang)
        return new
    except Exception:  # noqa
        return mk


_LANG = {}   # uid -> "bn" | "en" | ""   ("" = user has not chosen yet)


def _lang_row(uid):
    v = _LANG.get(uid)
    if v is None:
        r = db_q1("SELECT lang FROM users WHERE id=?", (uid,))
        if r is None:
            return None
        v = r["lang"] or ""
        _LANG[uid] = v
    return v


def get_lang(uid):
    return "en" if isinstance(uid, int) and uid > 0 and _lang_row(uid) == "en" else "bn"


def lang_unset(uid):
    return _lang_row(uid) == ""


def set_lang(uid, lang):
    db_update_user(uid, lang=lang)
    _LANG[uid] = lang


# ---- every outgoing message goes through the translator (language of the receiving chat)
_orig_send_message = bot.send_message
_orig_edit_message_text = bot.edit_message_text
_orig_send_document = bot.send_document


def _send_message(chat_id, text, *a, **kw):
    lang = get_lang(chat_id)
    if lang == "en":
        text = tr(text, lang)
        if "reply_markup" in kw:
            kw["reply_markup"] = tr_markup(kw["reply_markup"], lang)
    return _orig_send_message(chat_id, text, *a, **kw)


def _edit_message_text(text, chat_id=None, *a, **kw):
    lang = get_lang(chat_id)
    if lang == "en":
        text = tr(text, lang)
        if "reply_markup" in kw:
            kw["reply_markup"] = tr_markup(kw["reply_markup"], lang)
    return _orig_edit_message_text(text, chat_id, *a, **kw)


def _send_document(chat_id, *a, **kw):
    lang = get_lang(chat_id)
    if lang == "en":
        if isinstance(kw.get("caption"), str):
            kw["caption"] = tr(kw["caption"], lang)
        if "reply_markup" in kw:
            kw["reply_markup"] = tr_markup(kw["reply_markup"], lang)
    return _orig_send_document(chat_id, *a, **kw)


def _caption_wrapper(orig):
    def w(chat_id, *a, **kw):
        lang = get_lang(chat_id)
        if lang == "en":
            if isinstance(kw.get("caption"), str):
                kw["caption"] = tr(kw["caption"], lang)
            if "reply_markup" in kw:
                kw["reply_markup"] = tr_markup(kw["reply_markup"], lang)
        return orig(chat_id, *a, **kw)
    return w


bot.send_video = _caption_wrapper(bot.send_video)
bot.send_animation = _caption_wrapper(bot.send_animation)
bot.send_message = _send_message
bot.edit_message_text = _edit_message_text
bot.send_document = _send_document

# reply-keyboard buttons come back as text: map the English label back to the Bengali menu key
_MENU_REV = {tr(_m, "en"): _m for _m in (M_PANEL, M_FILES, M_UPGRADE, M_ACCOUNT, M_REFER, M_DOCS, M_SUPPORT, M_ADMIN, M_LANG)}


def lang_picker(first=False):
    sfx = ":1" if first else ""
    return ("🌐 <b>Choose your language / ভাষা বেছে নিন</b>",
            kb([("🇧🇩 বাংলা", f"lang:bn{sfx}"), ("🇬🇧 English", f"lang:en{sfx}")]))


def _continue_start(uid, chat_id):
    """What /start does once the language is known: channel check -> welcome (+ VPLink token)."""
    if not is_admin(uid) and not is_member(uid):
        return send_join(chat_id)
    complete_verification(uid, chat_id)
    pending = _vp_pending.pop(uid, None)
    if pending:
        redeem_vplink(uid, chat_id, pending)


@cb("lang", gate=False)
def c_lang(call, a):
    uid = call.from_user.id
    new = a[0] if a and a[0] in ("bn", "en") else "bn"
    set_lang(uid, new)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:  # noqa
        pass
    if len(a) > 1 and a[1] == "1":
        return _continue_start(uid, call.message.chat.id)
    bot.send_message(call.message.chat.id,
                     "✅ ভাষা সেট হয়েছে: <b>বাংলা</b>" if new == "bn" else "✅ Language set: <b>English</b>",
                     reply_markup=main_menu(uid))


@cb("langm", gate=False)
def c_langm(call, a):
    show(call, *lang_picker())


@menu(M_LANG)
def m_lang(m):
    show(m, *lang_picker())


# ==============================================================================
# 6. PROCESSES  (run / monitor / limits / backups for user projects)
# ==============================================================================
try:
    import resource
except ImportError:  # non-posix
    resource = None


def notify_owner(uid, text, pid=None):
    """Message a project owner; with 📋 লগ / 🎛 প্যানেল buttons when a project id is given."""
    markup = kb([("📋 লগ", f"pl:{pid}"), ("🎛 প্যানেল", f"p:{pid}")]) if pid else None
    notify(uid, text, markup)


RUNNING = {}          # project_id -> Popen
STARTING = {}         # project_id -> time the start began
START_TTL = 1200      # a start older than 20 min is considered stuck and may be replaced


def is_starting(pid):
    t = STARTING.get(pid)
    return bool(t) and time.time() - t < START_TTL
_ps_cache = {}
_high_cpu = {}
_restarts = {}
_lock = threading.RLock()

APP_FILE = str(Path(__file__).resolve())   # this very file — also acts as the sandboxed file worker
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RESERVED_ENV = {"PATH", "PYTHONPATH", "HOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONHOME"}
ENTRY_CANDIDATES = ["main.py", "bot.py", "app.py", "index.py", "run.py", "start.py", "__main__.py"]


# ------------------------------------------------------------------ paths / isolation
def iso_uid(user_id):
    if ISOLATE and hasattr(os, "geteuid") and os.geteuid() == 0:
        return ISO_BASE_UID + (int(user_id) % 2_000_000_000)
    return None


def project_dir(p):
    return PROJECTS_DIR / str(p["user_id"]) / str(p["id"])


def files_dir(p):
    return project_dir(p) / "files"


def libs_dir(p):
    return project_dir(p) / ".pylibs"


def log_path(p):
    return LOGS_DIR / f"{p['id']}.log"


def init_permissions():
    if hasattr(os, "geteuid") and os.geteuid() == 0 and ISOLATE:
        for d in (DATA_DIR, PROJECTS_DIR):
            os.chmod(d, 0o711)
        for d in (LOGS_DIR, BACKUP_DIR):
            os.chmod(d, 0o700)
        if DB_PATH.exists():
            os.chmod(DB_PATH, 0o600)
        log.info("User isolation ENABLED (per-user uid)")
    else:
        log.warning("User isolation DISABLED — user code can read the bot's files. Run as root or use Docker.")


def prepare_dirs(p):
    dirs = (project_dir(p).parent, project_dir(p), files_dir(p), libs_dir(p), files_dir(p) / "data")
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    uid = iso_uid(p["user_id"])
    if uid is not None:
        for d in dirs:
            os.chown(d, uid, uid)
            os.chmod(d, 0o700)


def _make_preexec(user_id):
    uid = iso_uid(user_id)

    def fn():
        if resource:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            fsize = MAX_FILE_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        if uid is not None:
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
            if resource:
                resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
    return fn


def _env(p, extra=None):
    e = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(project_dir(p)),
         "LANG": "C.UTF-8"}
    e.update(extra or {})
    return e


def run_as(p, cmd, cwd=None, env=None, timeout=60, stdin=None, pass_fds=()):
    """Run a command as the project owner's unprivileged OS user (sanitized env)."""
    return subprocess.run(cmd, cwd=cwd, env=env or _env(p), input=stdin, capture_output=True, timeout=timeout,
                          preexec_fn=_make_preexec(p["user_id"]), pass_fds=pass_fds)


def fs(p, op, rel="", *extra, stdin=None, base=None, timeout=120, pass_fds=()):
    """Sandboxed file op. Returns (True, dict|bytes) or (False, error_text)."""
    base = str(base or files_dir(p))
    cmd = [sys.executable, APP_FILE, "--fsworker", op, base, rel, *map(str, extra)]
    try:
        r = run_as(p, cmd, cwd=base, stdin=stdin, timeout=timeout, pass_fds=pass_fds)
    except subprocess.TimeoutExpired:
        return False, "টাইমআউট"
    except Exception as e:  # noqa
        return False, str(e)
    if op == "read":
        return (True, r.stdout) if r.returncode == 0 else (False, r.stderr.decode(errors="replace").strip())
    try:
        d = json.loads(r.stdout.decode() or "{}")
    except Exception:  # noqa
        return False, (r.stderr.decode(errors="replace") or "internal error")[-300:]
    return (True, d) if d.get("ok") else (False, d.get("error", "error"))


def disk_usage(p) -> int:
    ok, d = fs(p, "du", "", base=project_dir(p))
    return d["size"] if ok else 0


def detect_entry(p):
    ok, d = fs(p, "find_py")
    if not ok:
        return None
    files = d["files"]
    for c in ENTRY_CANDIDATES:
        if c in files:
            return c
    for f in files:
        if os.path.basename(f) in ENTRY_CANDIDATES:
            return f
    return files[0] if len(files) == 1 else None


# ------------------------------------------------------------------ pip / git
def pip_install(p, args, timeout=900):
    cmd = [sys.executable, "-m", "pip", "install", "--target", str(libs_dir(p)), "--no-cache-dir",
           "--disable-pip-version-check", "--upgrade", *args]
    try:
        r = run_as(p, cmd, cwd=str(files_dir(p)), env=_env(p, {"PIP_NO_INPUT": "1"}), timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "Timeout (15 মিনিট)"
    out = ANSI.sub("", (r.stdout + r.stderr).decode(errors="replace"))
    return r.returncode == 0, out[-3000:]


def git_sync(p, url):
    fdir = str(files_dir(p))
    env = _env(p, {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
    g = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never"]
    steps = []
    if not (files_dir(p) / ".git").exists():
        steps += [g + ["init", "-q"], g + ["remote", "add", "origin", url]]
    else:
        steps += [g + ["remote", "set-url", "origin", url]]
    steps += [g + ["fetch", "--depth", "1", "origin", "HEAD"], g + ["reset", "--hard", "FETCH_HEAD"]]
    out = ""
    for cmd in steps:
        try:
            r = run_as(p, cmd, cwd=fdir, env=env, timeout=180)
        except FileNotFoundError:
            return False, "সার্ভারে git ইন্সটল নেই"
        except subprocess.TimeoutExpired:
            return False, "Timeout"
        out = (r.stdout + r.stderr).decode(errors="replace")
        if r.returncode != 0:
            return False, re.sub(r"https://[^@\s]+@", "https://***@", out)[-1500:]
    return True, out[-500:]


# ------------------------------------------------------------------ start / stop
def is_running(pid) -> bool:
    pr = RUNNING.get(pid)
    return pr is not None and pr.poll() is None


# ------------------------------------------------------------------ RAM guard
# .env: RAM_GUARD_PCT=85  (server RAM % at which NEW projects are refused; 0 = guard off)
try:
    RAM_GUARD_PCT = max(0, min(99, int(os.getenv("RAM_GUARD_PCT", "85"))))
except ValueError:
    RAM_GUARD_PCT = 85
_ram_alert_at = 0.0


def ram_pressure():
    """(percent, used_mb, limit_mb) of the RAM this bot's server/container really has."""
    hr = hosting_ram()
    if hr:
        used, limit = hr
    else:
        vm = psutil.virtual_memory()
        used, limit = vm.total - vm.available, vm.total
    return used / limit * 100, used / 1048576, limit / 1048576


def _ram_alert(pct, used, limit, why):
    """Warn the admins (at most once per 30 minutes) and list the biggest running projects."""
    global _ram_alert_at
    now = time.time()
    if now - _ram_alert_at < 1800:
        return
    _ram_alert_at = now
    rows = []
    for pid, proc in list(RUNNING.items()):
        try:
            p = db_get_project(pid)
            if p and proc.poll() is None:
                rows.append((_measure(proc.pid)[0], p))
        except Exception:  # noqa
            pass
    rows.sort(key=lambda x: -x[0])
    top = "\n".join(f"{i}. {esc(p['name'])} • <code>{p['user_id']}</code> • {r:.0f} MB"
                    for i, (r, p) in enumerate(rows[:3], 1)) or "—"
    extra = "নতুন প্রজেক্ট চালু আপাতত বন্ধ রাখা হয়েছে।" if why == "blocked" else "RAM লিমিটের কাছাকাছি — নতুন প্রজেক্ট চালু বন্ধ থাকবে।"
    try:
        notify_admins(f"⚠️ <b>সার্ভারের RAM {pct:.0f}%</b> ({used:.0f} / {limit:.0f} MB)\n"
                      f"<blockquote>{extra}\n\n🏋️ সবচেয়ে বেশি RAM:\n{top}</blockquote>")
    except Exception:  # noqa
        pass


def ram_guard_block():
    """Message (str) when the server is too full to start one more project, otherwise None."""
    if RAM_GUARD_PCT <= 0:
        return None
    try:
        pct, used, limit = ram_pressure()
    except Exception:  # noqa
        return None
    if pct < RAM_GUARD_PCT:
        return None
    _ram_alert(pct, used, limit, "blocked")
    return f"⛔ সার্ভার এখন ব্যস্ত (RAM {pct:.0f}% ব্যবহৃত)। নতুন প্রজেক্ট এই মুহূর্তে চালু করা যাচ্ছে না — কিছুক্ষণ পর আবার চেষ্টা করুন।"


_ram_watch_at = 0.0


def _ram_watch():
    """Called by the monitor: warn admins when RAM stays above the guard level."""
    global _ram_watch_at
    now = time.time()
    if RAM_GUARD_PCT <= 0 or now - _ram_watch_at < 30:
        return
    _ram_watch_at = now
    pct, used, limit = ram_pressure()
    if pct >= RAM_GUARD_PCT:
        _ram_alert(pct, used, limit, "high")


def _auto_restart(pid):
    p = db_get_project(pid)
    if not p:
        return
    ok, info = start_project(p)
    if not ok and "সার্ভার এখন ব্যস্ত" in str(info):
        notify_owner(p["user_id"], f"⛔ <b>{esc(p['name'])}</b> অটো-রিস্টার্ট হয়নি।\n{esc(info)}", pid)


def start_project(p, guard=True):
    """Blocking. Returns (True, os_pid) or (False, error_text)."""
    pid = p["id"]
    if guard and not is_running(pid) and not is_starting(pid):
        blocked = ram_guard_block()
        if blocked:
            return False, blocked
    with _lock:
        if is_starting(pid):
            return False, "⏳ আগের স্টার্ট এখনো চলছে (requirements ইন্সটল হতে সময় লাগে)। শেষ হলে নিজে থেকেই চালু হবে — একটু পরে 🔄 রিফ্রেশ দিন।"
        if is_running(pid):
            return False, "এটি ইতোমধ্যে চালু আছে।"
        STARTING[pid] = t0 = time.time()
    try:
        return _start(pid, t0)
    except Exception as e:  # noqa
        log.exception("start failed")
        return False, f"{type(e).__name__}: {e}"
    finally:
        with _lock:
            if STARTING.get(pid) == t0:
                STARTING.pop(pid, None)


def _start(pid, t0):
    p = db_get_project(pid)
    prepare_dirs(p)
    entry = p["entry"] or detect_entry(p)
    if not entry:
        return False, "কোনো এন্ট্রি ফাইল (যেমন main.py) পাওয়া যায়নি। ফাইল আপলোড করুন অথবা '📝 এন্ট্রি ফাইল' থেকে বেছে নিন।"
    ok, err = fs(p, "exists", entry)
    if not ok:
        return False, err
    db_update_project(pid, entry=entry)
    _data_autorestore(p)          # lost data files come back from the last data backup

    ok, data = fs(p, "read", "requirements.txt", 1_000_000)
    if ok and data.strip():
        h = hashlib.md5(data).hexdigest()
        if h != p["req_hash"]:
            notify(p["user_id"], f"📦 <b>{esc(p['name'])}</b>: requirements.txt ইন্সটল হচ্ছে… ১-২ মিনিট লাগতে পারে, শেষ হলে অটো চালু হবে।")
            ok2, out = pip_install(p, ["-r", "requirements.txt"])
            if not ok2:
                return False, "❌ requirements.txt ইন্সটল ব্যর্থ:\n" + out[-1500:]
            db_update_project(pid, req_hash=h)

    env = {r["k"]: r["v"] for r in db_get_envs(pid) if r["k"] not in RESERVED_ENV}
    env.update(_env(p, {"PYTHONPATH": str(libs_dir(p)), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}))
    env["PROJECT_DATA_DIR"] = env["PYHOST_DATA_DIR"] = str(files_dir(p) / "data")   # persistent, auto-backed-up

    if STARTING.get(pid) != t0:      # ⏹ pressed (or replaced) while installing
        return False, "স্টার্ট বাতিল করা হয়েছে।"
    lp = log_path(p)
    if lp.exists() and lp.stat().st_size > 5 * 1024 * 1024:
        lp.write_bytes(lp.read_bytes()[-1024 * 1024:])
    with open(lp, "ab", buffering=0) as lf:
        lf.write(f"\n===== START {time.strftime('%F %T')} | {entry} =====\n".encode())
        proc = subprocess.Popen([sys.executable, "-u", entry], cwd=str(files_dir(p)), env=env,
                                stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                                start_new_session=True, preexec_fn=_make_preexec(p["user_id"]))
    with _lock:
        RUNNING[pid] = proc
    db_update_project(pid, status="running", pid=proc.pid, started_at=time.time())
    return True, proc.pid


def _kill_tree(proc_or_pid):
    pid = proc_or_pid.pid if hasattr(proc_or_pid, "pid") else proc_or_pid
    try:
        kids = psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        kids = []
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 3)):
        try:
            if pgid:
                os.killpg(pgid, sig)
            else:
                os.kill(pid, sig)
        except OSError:
            pass
        if hasattr(proc_or_pid, "wait"):
            try:
                proc_or_pid.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            time.sleep(1)
            break
    for k in kids:
        try:
            k.kill()
        except psutil.Error:
            pass


def stop_project(pid, status="stopped", bk=True):
    with _lock:
        proc = RUNNING.pop(pid, None)
        STARTING.pop(pid, None)       # also cancels a start that is still installing
    if proc is not None:
        _kill_tree(proc)
    _high_cpu.pop(pid, None)
    db_update_project(pid, status=status, pid=None)
    if bk and proc is not None:
        _data_bk_async(pid)


def delete_files(p):
    stop_project(p["id"], bk=False)
    for s in data_snapshots(p):
        s.unlink(missing_ok=True)
    shutil.rmtree(project_dir(p), ignore_errors=True)
    try:
        log_path(p).unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ stats / logs
def _get_ps(pid):
    pr = _ps_cache.get(pid)
    if pr is None or not pr.is_running():
        pr = psutil.Process(pid)
        _ps_cache[pid] = pr
        if len(_ps_cache) > 3000:
            for k in [k for k, v in _ps_cache.items() if not v.is_running()]:
                _ps_cache.pop(k, None)
    return pr


def _measure(os_pid):
    ram, cpu = 0.0, 0.0
    try:
        root = _get_ps(os_pid)
        for pr in [root] + root.children(recursive=True):
            try:
                pr = _get_ps(pr.pid)
                ram += pr.memory_info().rss
                cpu += pr.cpu_percent(None)
            except psutil.Error:
                pass
    except psutil.Error:
        pass
    return ram / 1048576, cpu


def proc_stats(p):
    proc = RUNNING.get(p["id"])
    if proc is not None and proc.poll() is None:
        ram, cpu = _measure(proc.pid)
        return {"running": True, "ram": ram, "cpu": cpu, "uptime": time.time() - (p["started_at"] or time.time())}
    return {"running": False, "ram": 0.0, "cpu": 0.0, "uptime": 0}


def tail_log(p, lines=40, maxchars=3000):
    lp = log_path(p)
    if not lp.exists():
        return ""
    with open(lp, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 65536))
        txt = f.read().decode(errors="replace")
    txt = ANSI.sub("", txt).replace("\r", "")
    return "\n".join(txt.splitlines()[-lines:])[-maxchars:]


def full_log(p) -> bytes:
    lp = log_path(p)
    if not lp.exists():
        return b""
    with open(lp, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 5 * 1024 * 1024))
        return ANSI.sub("", f.read().decode(errors="replace")).encode()


PIPMAP = {"telebot": "pyTelegramBotAPI", "cv2": "opencv-python-headless", "PIL": "pillow", "bs4": "beautifulsoup4",
          "yaml": "pyyaml", "dotenv": "python-dotenv", "telegram": "python-telegram-bot", "sklearn": "scikit-learn",
          "Crypto": "pycryptodome", "dns": "dnspython", "jwt": "pyjwt", "pyrogram": "pyrogram tgcrypto"}
HINTS = [
    (r"InvalidToken|Unauthorized|401", "বট টোকেন ভুল বা বাতিল হয়েছে — টোকেন চেক করুন।"),
    (r"terminated by other getUpdates|Conflict", "একই টোকেনে আরেকটি বট চলছে — অন্য জায়গায় বন্ধ করুন।"),
    (r"SyntaxError|IndentationError|TabError", "কোডে সিনট্যাক্স/ইন্ডেন্টেশন ত্রুটি আছে।"),
    (r"FileNotFoundError", "কোডে যে ফাইলটা খুঁজছে সেটা আপলোড করা হয়নি বা পাথ ভুল।"),
    (r"PermissionError", "ফাইল/ফোল্ডারে পারমিশন নেই — প্রজেক্ট ফোল্ডারের ভেতরে লিখুন।"),
    (r"MemoryError", "র‍্যাম শেষ — প্ল্যান আপগ্রেড করুন বা কোড অপ্টিমাইজ করুন।"),
    (r"Address already in use", "পোর্ট আগে থেকেই ব্যবহৃত হচ্ছে।"),
    (r"KeyError: '[A-Z_]+'", "সম্ভবত এনভায়রনমেন্ট ভেরিয়েবল সেট করা হয়নি — 🔐 এনভায়রনমেন্ট ভেরিয়েবল দেখুন।"),
]


def diagnose(text):
    m = re.search(r"No module named '([\w\.]+)'", text)
    if m:
        mod = m.group(1).split(".")[0]
        pkg = PIPMAP.get(mod, mod)
        return f"মডিউল <code>{esc(mod)}</code> নেই — 📦 Package Manager থেকে <code>{esc(pkg)}</code> ইন্সটল করুন।"
    for pat, hint in HINTS:
        if re.search(pat, text):
            return hint
    return None


# ------------------------------------------------------------------ backup
def make_backup(p):
    """Returns (Path, None) or (None, error)."""
    d = BACKUP_DIR / str(p["user_id"])
    d.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\-]", "_", p["name"])[:30] or "project"
    path = d / f"{p['id']}_{slug}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    with open(path, "w+b") as f:
        ok, res = fs(p, "zip", "", f.fileno(), pass_fds=(f.fileno(),), timeout=600)
    if not ok:
        path.unlink(missing_ok=True)
        return None, res
    olds = sorted(d.glob(f"{p['id']}_*.zip"))
    for o in olds[:-3]:
        o.unlink(missing_ok=True)
    return path, None


# ------------------------------------------------------------------ per-project data backup
def data_snapshots(p):
    d = BACKUP_DIR / "data" / str(p["user_id"])
    return sorted(d.glob(f"{p['id']}_*.zip")) if d.exists() else []


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_data_bk_lock = threading.Lock()


def data_backup(p):
    """Snapshot the project's data files (data/ + root .db/.json/.csv ...) outside the user's sandbox.
    Returns (path|None, status, info): status 'new' (info=file count) | 'same' | 'empty' | 'error' (info=text)."""
    with _data_bk_lock:
        d = BACKUP_DIR / "data" / str(p["user_id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{p['id']}_tmp.zip.part"
        try:
            with open(tmp, "w+b") as f:
                ok, res = fs(p, "zipdata", "", f.fileno(), DATA_BK_MAX_MB * 1024 * 1024,
                             pass_fds=(f.fileno(),), timeout=600)
            if not ok:
                return None, "error", res
            if res["count"] == 0:
                return None, "empty", 0
            snaps = data_snapshots(p)
            if snaps and _sha(snaps[-1]) == _sha(tmp):
                return snaps[-1], "same", res["count"]
            path = d / f"{p['id']}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            tmp.replace(path)
            for o in data_snapshots(p)[:-DATA_BK_KEEP]:
                o.unlink(missing_ok=True)
            return path, "new", res["count"]
        finally:
            tmp.unlink(missing_ok=True)


def _data_bk_async(p):
    def job():
        try:
            q = db_get_project(p) if isinstance(p, int) else p
            if q:
                data_backup(q)
        except Exception:  # noqa
            log.exception("data backup")
    threading.Thread(target=job, daemon=True, name="databk").start()


def data_restore(p, mode="missing"):
    """Put the latest data snapshot back. mode 'missing' keeps existing files, 'all' overwrites them.
    Returns (True, count) or (False, error_text)."""
    snaps = data_snapshots(p)
    if not snaps:
        return False, "no snapshot"
    ok, res = fs(p, "restoredata", "", mode, DATA_BK_MAX_MB * 1024 * 1024, stdin=snaps[-1].read_bytes(), timeout=600)
    return (True, res["count"]) if ok else (False, res)


def _data_autorestore(p):
    try:
        ok, n = data_restore(p, "missing")
        if ok and n:
            notify_owner(p["user_id"], f"♻️ <b>{esc(p['name'])}</b>: ব্যাকআপ থেকে {n}টি ডাটা ফাইল ফিরিয়ে আনা হয়েছে।", p["id"])
    except Exception:  # noqa
        log.exception("data autorestore")


def _data_backup_loop():
    time.sleep(180)
    while True:
        try:
            for p in db_projects_by_status("running"):
                if is_running(p["id"]):
                    data_backup(p)
        except Exception:  # noqa
            log.exception("data backup loop")
        time.sleep(max(5, DATA_BK_MINUTES) * 60)


# ------------------------------------------------------------------ monitor
def _handle_exit(p, code):
    pid = p["id"]
    with _lock:
        if RUNNING.pop(pid, None) is None:
            return                    # already handled by another thread
    _high_cpu.pop(pid, None)
    _data_bk_async(p)
    tail = tail_log(p, 200, 6000)
    if "===== START" in tail:
        tail = tail.rsplit("===== START", 1)[1].split("\n", 1)[-1]
    tail = "\n".join(tail.splitlines()[-25:])[-2500:]
    if code == 0:
        db_update_project(pid, status="stopped", pid=None)
        notify_owner(p["user_id"], f"ℹ️ <b>{esc(p['name'])}</b> স্বাভাবিকভাবে শেষ হয়েছে (exit 0)।\n"
                             "বট হলে সম্ভবত কোডে polling/loop নেই।", pid)
        return
    db_update_project(pid, status="crashed", pid=None)
    hint = diagnose(tail)
    msg = (head("❌", "প্রজেক্ট ক্র্যাশ করেছে", esc(p["name"]))
           + f"<blockquote>🔢 Exit code: <code>{code}</code></blockquote>\n"
             f"<pre>{esc(tail) or '(লগ খালি)'}</pre>")
    if hint:
        msg += f"\n💡 <b>সমাধান:</b> {hint}"
    plan = db_plan_state(db_get_user(p["user_id"]))
    if p["auto_restart"] and plan["plan"]["auto_restart"] and plan["active"]:
        now = time.time()
        hist = [t for t in _restarts.get(pid, []) if now - t < 600]
        if len(hist) < MAX_AUTO_RESTARTS:
            hist.append(now)
            _restarts[pid] = hist
            notify_owner(p["user_id"], msg + "\n\n♻️ ৫ সেকেন্ড পর অটো-রিস্টার্ট হবে…", pid)
            threading.Timer(5, _auto_restart, args=(pid,)).start()
            return
        msg += "\n\n⛔ বারবার ক্র্যাশ করায় অটো-রিস্টার্ট বন্ধ। কোড ঠিক করে নিজে চালু করুন।"
    notify_owner(p["user_id"], msg, pid)


def _tick():
    for pid, proc in list(RUNNING.items()):
        p = db_get_project(pid)
        if not p:
            with _lock:
                RUNNING.pop(pid, None)
            _kill_tree(proc)
            continue
        code = proc.poll()
        if code is not None:
            _handle_exit(p, code)
            continue
        st = db_plan_state(db_get_user(p["user_id"]))
        plan = st["plan"]
        if not st["active"]:
            stop_project(pid)
            notify_owner(p["user_id"], f"⏰ আপনার প্ল্যান/ট্রায়াল শেষ, তাই <b>{esc(p['name'])}</b> বন্ধ করা হয়েছে।\n"
                                 "💎 প্ল্যান আপগ্রেড করে আবার চালু করুন।", pid)
            continue
        ram, cpu = _measure(proc.pid)
        if ram > plan["ram_mb"]:
            stop_project(pid, "crashed")
            notify_owner(p["user_id"], f"💥 <b>{esc(p['name'])}</b> র‍্যাম লিমিট ({plan['ram_mb']} MB) ছাড়িয়েছে "
                                 f"({ram:.0f} MB), তাই বন্ধ করা হয়েছে।\nকোড অপ্টিমাইজ করুন বা প্ল্যান আপগ্রেড করুন।", pid)
            continue
        if cpu > plan["cpu_pct"]:
            _high_cpu[pid] = _high_cpu.get(pid, 0) + 1
            if _high_cpu[pid] >= CPU_STRIKES:
                stop_project(pid, "crashed")
                notify_owner(p["user_id"], f"🔥 <b>{esc(p['name'])}</b> দীর্ঘ সময় CPU লিমিট ({plan['cpu_pct']}%) "
                                     f"ছাড়িয়েছে ({cpu:.0f}%), তাই বন্ধ করা হয়েছে।", pid)
        else:
            _high_cpu[pid] = 0


def _monitor_loop():
    while True:
        try:
            _tick()
            _ram_watch()
        except Exception:  # noqa
            log.exception("monitor error")
        time.sleep(MONITOR_INTERVAL)


def start_monitor():
    threading.Thread(target=_monitor_loop, daemon=True, name="monitor").start()


def restore_projects():
    """After a bot/server restart: kill orphans and bring 'running' projects back up."""
    _n = 0
    for p in db_projects_by_status("running"):
        if p["pid"] and psutil.pid_exists(p["pid"]):
            try:
                pr = psutil.Process(p["pid"])
                if pr.cwd() == str(files_dir(p)):
                    _kill_tree(p["pid"])
            except psutil.Error:
                pass
        st = db_plan_state(db_get_user(p["user_id"]))
        if st["active"] and p["entry"]:
            # after a redeploy: bring everything back (no RAM guard), but one project every 2s so RAM does not spike
            _t = threading.Timer(_n * 2, start_project, args=(p, False))
            _t.daemon = True
            _t.start()
            _n += 1
        else:
            db_update_project(p["id"], status="stopped", pid=None)


# ==============================================================================
# 7. HANDLERS — user (/start, account, referral, docs, support)
# ==============================================================================
def welcome_message(name=None):
    """Title + body come from Admin Panel -> 👋 ওয়েলকাম মেসেজ (empty = default)."""
    title = (db_get_setting("welcome_title") or "").strip() or DEFAULT_SETTINGS["welcome_title"]
    body = (db_get_setting("welcome_text") or "").strip() or DEFAULT_SETTINGS["welcome_text"]
    free = db_get_plan("free")
    hello = f"👋 হ্যালো, <b>{esc(name)}</b>!\n\n" if name else ""
    return (f"✦ 🚀 <b>{esc(title)}</b>\n<i>প্রিমিয়াম পাইথন বট হোস্টিং</i>\n{DIV}\n"
            f"{hello}{esc(body)}\n\n{DIV}\n"
            f"<blockquote>🎁 ফ্রি প্ল্যান: <b>{free['max_projects']}টি বট</b>, প্রথম চালুর পর <b>{free['days']} দিন</b>।\n"
            f"💎 তারপর প্ল্যান কিনতে হবে — অথবা রেফার করে ব্যালেন্স আর্ন করুন!</blockquote>\n\n"
            f"🚀 শুরু করতে 🎛 <b>কন্ট্রোল প্যানেল</b> থেকে নতুন প্রজেক্ট বানান।")


def _vis_len(t):
    return len(html.unescape(re.sub(r"<[^>]+>", "", t)))


def send_welcome(chat_id, text, markup=None):
    """Welcome message together with the admin's welcome video (Admin Panel -> 👋 Welcome -> 🎬).
    Short text -> video with the text as caption; long text -> video first, then the text.
    Returns True when the video was delivered (falls back to plain text otherwise)."""
    v = (db_get_setting("welcome_video") or "").strip()
    if v:
        kind, _, ref = v.partition(":")
        send = bot.send_animation if kind == "animation" else bot.send_video
        try:
            if _vis_len(tr(text, get_lang(chat_id))) <= 1024:
                send(chat_id, ref, caption=text, reply_markup=markup)
            else:
                send(chat_id, ref)
                bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)
            return True
        except Exception as e:  # noqa
            log.info("welcome video failed: %s", e)
    bot.send_message(chat_id, text, reply_markup=markup)
    return False


def complete_verification(uid, chat_id):
    u = db_get_user(uid)
    first = not u["verified"]
    if first:
        db_update_user(uid, verified=1)
        reward = float(db_get_setting("referral_reward") or 0)
        ref = db_claim_referral(uid, reward)
        if ref:
            notify(ref, head("🎉", "নতুন রেফার!", "আপনার লিংকে একজন জয়েন করেছে")
                   + f"<blockquote>💰 <b>+৳{reward:g}</b> ব্যালেন্সে যোগ হয়েছে।</blockquote>")
    banner = "✅ <b>ভেরিফিকেশন সম্পন্ন!</b> আপনাকে স্বাগতম 🎉\n\n" if first else ""
    send_welcome(chat_id, banner + welcome_message(u["first_name"]), main_menu(uid))


@bot.message_handler(commands=["start"])
@_quick_cmd("start_cmd")
def cmd_start(m):
    uid = m.from_user.id
    ref = None
    parts = m.text.split(maxsplit=1)
    vp_token = None
    if len(parts) > 1 and parts[1].startswith("ref_") and parts[1][4:].isdigit():
        ref = int(parts[1][4:])
    elif len(parts) > 1 and parts[1].startswith("vp_"):
        vp_token = parts[1][3:]
    db_upsert_user(m.from_user, referred_by=ref)
    if db_get_user(uid)["banned"]:
        return bot.send_message(m.chat.id, "🚫 আপনাকে ব্যান করা হয়েছে।")
    clear_state(uid)
    if vp_token:
        _vp_pending[uid] = vp_token
    if lang_unset(uid):          # first time: choose the language, then continue
        return bot.send_message(m.chat.id, *lang_picker(first=True)[:1], reply_markup=lang_picker(first=True)[1])
    _continue_start(uid, m.chat.id)


@bot.message_handler(commands=["cancel", "help", "menu"])
def cmd_misc(m):
    clear_state(m.from_user.id)
    guarded(lambda mm: bot.send_message(mm.chat.id, "✅ ঠিক আছে। মেনু থেকে বেছে নিন।",
                                        reply_markup=main_menu(mm.from_user.id)))(m)


@cb("chk", gate=False)
def c_check(call, a):
    uid = call.from_user.id
    missing, broken = [], []
    if not is_admin(uid):
        _member_cache.pop(uid, None)          # always a fresh check when the button is pressed
        missing, broken = _channel_status(uid)
    if not missing and not broken:
        _member_cache[uid] = time.time()
        ack(call, "✅ ভেরিফাই সফল!")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:  # noqa
            pass
        complete_verification(uid, call.message.chat.id)
        pending = _vp_pending.pop(uid, None)
        if pending:
            redeem_vplink(uid, call.message.chat.id, pending)
    elif missing:
        ack(call, "❌ আপনি এখনো চ্যানেলে জয়েন করেননি!", True)
        show(call, *_join_screen(missing))
    else:                                     # bot cannot check the channel (not admin / wrong username)
        _warn_channel_admins(broken)
        ack(call, "⚠️ এই মুহূর্তে ভেরিফাই করা যাচ্ছে না। অ্যাডমিনকে জানানো হয়েছে — একটু পরে আবার চেষ্টা করুন।", True)


@menu(M_ACCOUNT)
def m_account(m):
    u = db_get_user(m.from_user.id)
    st = db_plan_state(u)
    cnt = len(db_user_projects(u["id"]))
    mx = st["plan"]["max_projects"]
    show(m, (head("👤", "আমার অ্যাকাউন্ট", "আপনার প্রোফাইল ও প্ল্যানের সারাংশ")
             + f"<blockquote>🆔 ID: <code>{u['id']}</code>\n"
               f"📅 জয়েন: {fmt_ts(u['joined_at'])}</blockquote>\n"
               f"<blockquote>👑 প্ল্যান: <b>{st['plan']['name']}</b>\n"
               f"⏳ বাকি: {plan_left(st)}\n"
               f"📦 প্রজেক্ট: <b>{cnt}/{mx}</b>  {bar(cnt, mx, 8)}\n"
               f"💾 RAM লিমিট: {st['plan']['ram_mb']} MB  •  🧠 CPU: {st['plan']['cpu_pct']}%</blockquote>\n"
               f"<blockquote>💰 ব্যালেন্স: <b>৳{u['balance']:g}</b>\n"
               f"👥 রেফার: <b>{u['ref_count']}</b> জন</blockquote>"),
         kb([("💎 প্ল্যান আপগ্রেড", "up"), ("🎁 রেফার", "ref")], [("🌐 ভাষা / Language", "langm")]))


@cb("ref")
def c_ref(call, a):
    m_refer(call)


@menu(M_REFER)
def m_refer(obj):
    uid = obj.from_user.id
    u = db_get_user(uid)
    reward = float(db_get_setting("referral_reward") or 0)
    link = f"https://t.me/{bot_username()}?start=ref_{uid}"
    show(obj, (head("🎁", "রেফার ও আর্ন", "বন্ধুদের ইনভাইট করুন, ব্যালেন্স জিতুন")
               + f"প্রতিটি সফল রেফারে পাবেন <b>৳{reward:g}</b>!\n"
                 f"<i>(রেফার করা ব্যক্তি চ্যানেল ভেরিফাই করলে ব্যালেন্স যোগ হবে)</i>\n\n"
                 f"🔗 <b>আপনার রেফার লিংক</b>\n<code>{link}</code>\n\n"
                 f"<blockquote>👥 মোট রেফার: <b>{u['ref_count']}</b>\n"
                 f"💰 ব্যালেন্স: <b>৳{u['balance']:g}</b></blockquote>\n\n"
                 f"💎 ব্যালেন্স দিয়ে প্ল্যান কিনতে পারবেন।"),
         kb([("📤 শেয়ার করুন", f"https://t.me/share/url?url={link}&text=ফ্রি বট হোস্টিং!")],
            [("💎 প্ল্যান কিনুন", "up")]))


@menu(M_DOCS)
def m_docs(m):
    show(m, (head("📖", "ডকুমেন্টেশন", "শুরু থেকে শেষ — ধাপে ধাপে গাইড")
             + "<b>1️⃣ প্রজেক্ট বানান</b>\n<blockquote>🎛 কন্ট্রোল প্যানেল → ➕ নতুন প্রজেক্ট</blockquote>\n"
             "<b>2️⃣ ফাইল আপলোড</b>\n<blockquote>.py ফাইল বা পুরো প্রজেক্টের .zip পাঠান (সর্বোচ্চ 20MB)। "
             "<code>requirements.txt</code> থাকলে চালুর সময় অটো ইন্সটল হবে।</blockquote>\n"
             "<b>3️⃣ টোকেন/সিক্রেট</b>\n<blockquote>🔐 এনভায়রনমেন্ট ভেরিয়েবলে সেট করুন, কোডে "
             "<code>os.getenv('BOT_TOKEN')</code> দিয়ে নিন।</blockquote>\n"
             "<b>4️⃣ চালু করুন</b>\n<blockquote>▶️ চালু করুন চাপুন। এরর হলে আমি সাথে সাথে লগ + সমাধান পাঠাব।</blockquote>\n"
             "<b>5️⃣ আরও টুল</b>\n<blockquote>📦 Package Manager — pip প্যাকেজ ইন্সটল\n🐙 গিটহাব সিঙ্ক\n"
             "💾 ব্যাকআপ — ZIP ডাউনলোড\n♻️ অটো-রিস্টার্ট (পেইড প্ল্যান)</blockquote>\n"
             "<b>⚠️ নিয়ম</b>\n<blockquote>লিমিটের বেশি RAM/CPU ব্যবহার করলে প্রজেক্ট বন্ধ হবে। "
             "অবৈধ/ক্ষতিকর কাজে ব্যবহার নিষিদ্ধ।</blockquote>"))


@menu(M_SUPPORT)
def m_support(m):
    sup = db_get_setting("support").strip()
    rows = [[("📢 অফিসিয়াল চ্যানেল", f"https://t.me/{channels()[0].lstrip('@')}")]] if channels() else []
    if sup:
        rows.insert(0, [("💬 সাপোর্টে মেসেজ", f"https://t.me/{sup.lstrip('@')}")])
    show(m, head("🛰", "সাপোর্ট হাব", "আমরা আছি আপনার পাশে")
            + "সমস্যা হলে নিচের বাটনে যোগাযোগ করুন।\n"
              "<blockquote>🧾 পেমেন্ট সমস্যায় TrxID সহ জানাবেন।</blockquote>"
            + (f"\n💬 সাপোর্ট: <b>{esc(sup)}</b>" if sup else ""),
         kb(*rows) if rows else None)


# ==============================================================================
# 8. HANDLERS — control panel (projects, start/stop, logs, backup, delete)
# ==============================================================================
def own(call, pid):
    try:
        p = db_get_project(int(pid))
    except (TypeError, ValueError):
        p = None
    if not p or (p["user_id"] != call.from_user.id and not is_admin(call.from_user.id)):
        ack(call, "❌ প্রজেক্ট পাওয়া যায়নি", True)
        return None
    return p


def owned_by(uid, pid):
    p = db_get_project(pid)
    return p if p and (p["user_id"] == uid or is_admin(uid)) else None


def panel_text(p, live_frame=None):
    u = db_get_user(p["user_id"])
    st = db_plan_state(u)
    plan = st["plan"]
    s = proc_stats(p)
    status = ("🟢 অনলাইন" if s["running"] else "🟡 চালু হচ্ছে…" if is_starting(p["id"])
              else "🟠 ক্র্যাশড" if p["status"] == "crashed" else "🔴 অফলাইন")
    ram = f"{s['ram']:.1f} / {plan['ram_mb']} MB" if s["running"] else f"— / {plan['ram_mb']} MB"
    ram_bar = bar(s["ram"], plan["ram_mb"]) if s["running"] else bar(0, 1)
    cpu_bar = bar(s["cpu"], plan["cpu_pct"]) if s["running"] else bar(0, 1)
    live_header = ""
    if live_frame:
        server_cpu = psutil.cpu_percent(interval=None)
        server_ram = psutil.virtual_memory()
        live_header = (f"📡 <b>LIVE MONITOR</b> {live_frame}\n"
                       f"<i>প্রতি ৪ সেকেন্ডে লাইভ আপডেট</i>\n"
                       f"<blockquote>🖥 <b>সার্ভার CPU:</b> {server_cpu:.1f}%\n{bar(server_cpu, 100)}\n"
                       f"🖥 <b>সার্ভার RAM:</b> {human_size(server_ram.used)} / {human_size(server_ram.total)}\n"
                       f"{bar(server_ram.percent, 100)}</blockquote>\n{DIV}\n")
    return (live_header + f"⚙️ <b>প্রজেক্ট:</b> <code>{esc(p['name'])}</code>\n{DIV}\n"
            f"<blockquote>📡 স্ট্যাটাস: <b>{status}</b>\n"
            f"💾 র‍্যাম: {ram}\n{ram_bar}\n"
            f"🧠 সিপিউ: {s['cpu']:.1f}% / {plan['cpu_pct']}%\n{cpu_bar}\n"
            f"⬆️ আপটাইম: {fmt_dur(s['uptime']) if s['running'] else '0m'}\n"
            f"📝 এন্ট্রি: {esc(p['entry'] or 'সেট করা হয়নি')}</blockquote>\n"
            f"<blockquote>👑 প্ল্যান: <b>{plan['name']}</b>\n⏳ বাকি সময়: {plan_left(st)}</blockquote>")


def panel_kb(p):
    pid = p["id"]
    rows = [[("🔄 রিফ্রেশ", f"p:{pid}"), ("📋 লগ দেখুন", f"pl:{pid}")]]
    if is_running(pid):
        rows.append([("🔁 রিস্টার্ট", f"prs:{pid}"), ("⏹ বন্ধ করুন", f"px:{pid}")])
    elif is_starting(pid):
        rows.append([("⏳ চালু হচ্ছে… রিফ্রেশ", f"p:{pid}"), ("⏹ বাতিল", f"px:{pid}")])
    else:
        rows.append([("▶️ চালু করুন", f"ps:{pid}")])
    rows += [[("🐙 গিটহাব সিঙ্ক", f"pg:{pid}"), ("📝 এন্ট্রি ফাইল", f"pen:{pid}")],
             [("📁 ফাইলস", f"fm:{pid}"), ("💾 ব্যাকআপ", f"pbk:{pid}")],
             [("🗄 ডাটা ব্যাকআপ", f"pdb:{pid}")],
             [("📦 Package Manager", f"ppm:{pid}")],
             [("🔐 এনভায়রনমেন্ট ভেরিয়েবল", f"pev:{pid}")],
             [(f"♻️ অটো-রিস্টার্ট: {'ON' if p['auto_restart'] else 'OFF'}", f"par:{pid}"),
              ("🗑 ডিলিট", f"pdl:{pid}")],
             [("📡 Live CPU/RAM Monitor", f"plive:{pid}")],
             [("◀️ পেছনে যান", "cp")]]
    return kb(*rows)


# Live dashboards are isolated from the hosting/worker logic. Each one edits only
# the existing Telegram panel message and samples the project's real CPU/RAM use.
_LIVE_MONITORS = {}
_LIVE_MONITORS_LOCK = threading.Lock()
_LIVE_FRAMES = ("◴", "◷", "◶", "◵")


def _live_monitor_keyboard(p):
    markup = panel_kb(p)
    markup.row(types.InlineKeyboardButton("⏹ Live Monitor বন্ধ", callback_data=f"ploff:{p['id']}"))
    return markup


def _live_monitor_loop(chat_id, message_id, pid, stop_event):
    frame = 0
    try:
        while not stop_event.is_set():
            p = db_get_project(pid)
            if not p:
                break
            text = panel_text(p, _LIVE_FRAMES[frame % len(_LIVE_FRAMES)])
            try:
                bot.edit_message_text(text, chat_id, message_id,
                                      reply_markup=_live_monitor_keyboard(p),
                                      disable_web_page_preview=True)
            except ApiTelegramException as exc:
                if "message is not modified" not in str(exc):
                    log.info("live monitor update stopped: %s", exc)
                    break
            except Exception as exc:  # live UI must never affect project processes
                log.info("live monitor update stopped: %s", exc)
                break
            frame += 1
            if stop_event.wait(4.0):
                break
    finally:
        with _LIVE_MONITORS_LOCK:
            if _LIVE_MONITORS.get((chat_id, pid)) is stop_event:
                _LIVE_MONITORS.pop((chat_id, pid), None)


def show_panel(obj, p, extra=""):
    p = db_get_project(p["id"])
    show(obj, panel_text(p) + extra, panel_kb(p))


# ------------------------------------------------------------------ list / create
@menu(M_PANEL)
def m_panel(m):
    show_projects(m)


@cb("cp")
def c_cp(call, a):
    clear_state(call.from_user.id)
    show_projects(call)


def show_projects(obj):
    uid = obj.from_user.id
    u = db_get_user(uid)
    st = db_plan_state(u)
    projs = db_user_projects(uid)
    rows = []
    for p in projs:
        dot = "🟢" if is_running(p["id"]) else "🟡" if is_starting(p["id"]) else "🟠" if p["status"] == "crashed" else "🔴"
        rows.append([(f"{dot} {p['name']}", f"p:{p['id']}")])
    rows.append([("➕ নতুন প্রজেক্ট", "np")])
    mx = st["plan"]["max_projects"]
    show(obj, (head("🎛", "কন্ট্রোল প্যানেল", "আপনার সব প্রজেক্ট এক জায়গায়")
               + f"<blockquote>👑 প্ল্যান: <b>{st['plan']['name']}</b> • ⏳ {plan_left(st)}\n"
                 f"📦 প্রজেক্ট: <b>{len(projs)}/{mx}</b>  {bar(len(projs), mx, 8)}</blockquote>\n\n"
               + ("আপনার প্রজেক্ট বেছে নিন 👇" if projs else "কোনো প্রজেক্ট নেই। ➕ নতুন প্রজেক্ট বানান।")),
         kb(*rows))


@cb("np")
def c_new(call, a):
    uid = call.from_user.id
    st = db_plan_state(db_get_user(uid))
    if len(db_user_projects(uid)) >= st["plan"]["max_projects"]:
        return ack(call, f"⛔ আপনার প্ল্যানে সর্বোচ্চ {st['plan']['max_projects']}টি প্রজেক্ট। প্ল্যান আপগ্রেড করুন।", True)
    set_state(uid, "new_name")
    show(call, head("📝", "নতুন প্রজেক্টের নাম লিখুন", "একটি ছোট ও সহজ নাম দিন")
               + "<blockquote>২-৩০ অক্ষর • ইংরেজি/সংখ্যা/স্পেস/-/_</blockquote>\n\nবাতিল: /cancel")


@state("new_name")
def s_new_name(m, st):
    uid = m.from_user.id
    name = (m.text or "").strip() if m.content_type == "text" else ""
    if not re.fullmatch(r"[\w \-]{2,30}", name, flags=re.ASCII):
        return bot.send_message(m.chat.id, "❌ নাম ঠিক নয়। ২-৩০ অক্ষর, শুধু ইংরেজি/সংখ্যা/স্পেস/-/_ ব্যবহার করুন।")
    plan = db_plan_state(db_get_user(uid))["plan"]
    if len(db_user_projects(uid)) >= plan["max_projects"]:
        clear_state(uid)
        return bot.send_message(m.chat.id, "⛔ প্রজেক্ট লিমিট শেষ।")
    pid = db_create_project(uid, name)
    prepare_dirs(db_get_project(pid))
    set_state(uid, "upload", pid=pid, rel="")
    bot.send_message(m.chat.id, head("✅", "প্রজেক্ট তৈরি হয়েছে!", esc(name))
                     + "📤 এখন আপনার <code>.py</code> ফাইল বা পুরো প্রজেক্টের <code>.zip</code> পাঠান।\n"
                       "<blockquote>requirements.txt থাকলে অটো ইন্সটল হবে। একাধিক ফাইল পাঠাতে পারেন।</blockquote>",
                     reply_markup=kb([("✅ আপলোড শেষ", f"fupd:{pid}")]))


@cb("p")
def c_panel(call, a):
    p = own(call, a[0])
    if p:
        clear_state(call.from_user.id)
        show_panel(call, p)


@cb("plive")
def c_live_monitor(call, a):
    p = own(call, a[0])
    if not p:
        return
    key = (call.message.chat.id, p["id"])
    with _LIVE_MONITORS_LOCK:
        previous = _LIVE_MONITORS.get(key)
        if previous:
            previous.set()
        stop_event = threading.Event()
        _LIVE_MONITORS[key] = stop_event
    clear_state(call.from_user.id)
    show(call, panel_text(p, _LIVE_FRAMES[0]), _live_monitor_keyboard(p))
    threading.Thread(target=_live_monitor_loop,
                     args=(call.message.chat.id, call.message.message_id, p["id"], stop_event),
                     daemon=True, name=f"live-monitor-{p['id']}").start()


@cb("ploff")
def c_live_monitor_off(call, a):
    p = own(call, a[0])
    if not p:
        return
    key = (call.message.chat.id, p["id"])
    with _LIVE_MONITORS_LOCK:
        stop_event = _LIVE_MONITORS.pop(key, None)
    if stop_event:
        stop_event.set()
    show_panel(call, p)


# ------------------------------------------------------------------ start / stop / restart
def _start_job(chat_id, pid, prefix=""):
    with work(chat_id, "start"):
        return _start_job_inner(chat_id, pid, prefix)


def _start_job_inner(chat_id, pid, prefix=""):
    p = db_get_project(pid)
    if not p:
        return
    ok, info = start_project(p)
    if ok:
        time.sleep(2)     # catch an instant crash (missing module, wrong token, syntax error...) right here
        pr = RUNNING.get(pid)
        if pr is None or pr.poll() is not None:
            if pr is not None:
                _handle_exit(db_get_project(pid), pr.poll())   # sends the crash log + fix hint
            return
        p = db_get_project(pid)
        bot.send_message(chat_id, panel_text(p) + f"\n{prefix}✅ <b>প্রজেক্ট চালু হয়েছে!</b>\n🆔 PID: <code>{info}</code>",
                         reply_markup=panel_kb(p))
    else:
        bot.send_message(chat_id, f"❌ <b>চালু করা যায়নি:</b>\n<pre>{esc(str(info)[-2500:])}</pre>",
                         reply_markup=panel_kb(p))


def _can_start(call, p):
    u = db_get_user(p["user_id"])
    st = db_plan_state(u)
    if not st["active"]:
        ack(call, "⛔ ফ্রি ট্রায়াল/প্ল্যান শেষ। 💎 প্ল্যান কিনুন।", True)
        return None
    if db_running_count(p["user_id"]) >= st["plan"]["max_projects"] and not is_running(p["id"]):
        ack(call, "⛔ প্ল্যানের লিমিটের বেশি প্রজেক্ট একসাথে চালানো যাবে না।", True)
        return None
    prefix = ""
    if not st["paid"] and u["trial_started"] is None:
        db_update_user(u["id"], trial_started=time.time())
        prefix = f"🎁 ফ্রি ট্রায়াল শুরু! শেষ হবে: <b>{fmt_ts(time.time() + st['plan']['days'] * 86400)}</b>\n"
    return prefix


@cb("ps")
def c_start(call, a):
    p = own(call, a[0])
    if not p:
        return
    if is_running(p["id"]):
        return ack(call, "ইতোমধ্যে চলছে")
    if is_starting(p["id"]):
        return ack(call, "⏳ চালু হচ্ছে — requirements ইন্সটল হতে সময় লাগে। একটু পরে 🔄 রিফ্রেশ দিন।", True)
    prefix = _can_start(call, p)
    if prefix is None:
        return
    ack(call, "⏳ চালু হচ্ছে…")
    threading.Thread(target=_start_job, args=(call.message.chat.id, p["id"], prefix), daemon=True).start()


@cb("px")
def c_stop(call, a):
    p = own(call, a[0])
    if p:
        stop_project(p["id"])
        ack(call, "⏹ বন্ধ করা হয়েছে")
        show_panel(call, p)


@cb("prs")
def c_restart(call, a):
    p = own(call, a[0])
    if not p:
        return
    prefix = _can_start(call, p)
    if prefix is None:
        return
    ack(call, "🔁 রিস্টার্ট হচ্ছে…")

    def job():
        stop_project(p["id"])
        _start_job(call.message.chat.id, p["id"], prefix)
    threading.Thread(target=job, daemon=True).start()


@cb("par")
def c_autorestart(call, a):
    p = own(call, a[0])
    if not p:
        return
    plan = db_plan_state(db_get_user(p["user_id"]))["plan"]
    if not plan["auto_restart"]:
        return ack(call, "♻️ অটো-রিস্টার্ট শুধু পেইড প্ল্যানে পাওয়া যায়। 💎 আপগ্রেড করুন।", True)
    db_update_project(p["id"], auto_restart=0 if p["auto_restart"] else 1)
    show_panel(call, p)


# ------------------------------------------------------------------ logs
@cb("pl")
def c_logs(call, a):
    p = own(call, a[0])
    if not p:
        return
    txt = tail_log(p, 40, 3000)
    show(call, f"📋 <b>লগ — {esc(p['name'])}</b>\n<pre>{esc(txt) if txt.strip() else '(লগ খালি)'}</pre>",
         kb([("🔄 রিফ্রেশ", f"pl:{p['id']}"), ("📥 ফুল লগ", f"pld:{p['id']}")], [("◀️ পেছনে", f"p:{p['id']}")]))


@cb("pld")
def c_logfile(call, a):
    p = own(call, a[0])
    if not p:
        return
    data = full_log(p)
    if not data:
        return ack(call, "লগ খালি", True)
    bot.send_document(call.message.chat.id, (f"{p['name']}_log.txt", data))


# ------------------------------------------------------------------ backup
@cb("pbk")
def c_backup(call, a):
    p = own(call, a[0])
    if not p:
        return
    ack(call, "💾 ব্যাকআপ তৈরি হচ্ছে…")

    def job():
        with work(call.message.chat.id, "backup"):
            path, err = make_backup(p)
        if not path:
            return bot.send_message(call.message.chat.id, f"❌ ব্যাকআপ ব্যর্থ: {esc(err)}")
        size = path.stat().st_size
        if size > 49 * 1024 * 1024:
            return bot.send_message(call.message.chat.id, "⚠️ ব্যাকআপ ৫০MB এর বেশি, টেলিগ্রামে পাঠানো যাচ্ছে না।")
        with open(path, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption=f"💾 <b>{esc(p['name'])}</b> ব্যাকআপ • {fmt_ts(time.time())}\n"
                              "রিস্টোর করতে: ফাইল ম্যানেজারে এই ZIP আপলোড করুন।")
    threading.Thread(target=job, daemon=True).start()


# ------------------------------------------------------------------ data backup (per project)
def data_text(p, note=""):
    snaps = data_snapshots(p)
    if snaps:
        last = snaps[-1]
        info = (f"🕒 সর্বশেষ ব্যাকআপ: {fmt_ts(last.stat().st_mtime)}\n"
                f"📦 সাইজ: {human_size(last.stat().st_size)}  •  🗂 কপি: {len(snaps)}/{DATA_BK_KEEP}")
    else:
        info = "🕒 এখনো কোনো ডাটা ব্যাকআপ নেই"
    return (head("🗄", "ডাটা ব্যাকআপ", esc(p["name"]))
            + (note + "\n\n" if note else "")
            + "<blockquote>📂 ডাটা ফোল্ডার: <code>data/</code>\n"
              "🔑 কোডে পাথ: <code>PROJECT_DATA_DIR</code> (env)</blockquote>\n"
            + f"<blockquote>{info}</blockquote>\n\n"
            + "কোডের ডাটা (db / json / csv ...) <code>data/</code> ফোল্ডারে রাখুন — অটো ব্যাকআপ হবে। "
              "ডাটা হারিয়ে গেলেও প্রজেক্ট চালু করলেই সব ফিরে আসবে।\n"
              "<blockquote>ℹ️ ফোল্ডারের বাইরের <code>.db .sqlite .json .csv .pkl</code> ফাইলও ব্যাকআপে ধরা হয়। "
              f"চালু অবস্থায় প্রতি {DATA_BK_MINUTES} মিনিটে এবং বন্ধ হলে অটো ব্যাকআপ হয়।</blockquote>")


def data_kb(p):
    i = p["id"]
    return kb([("💾 এখনই ব্যাকআপ", f"pdbn:{i}"), ("📥 ডাটা ডাউনলোড", f"pdbd:{i}")],
              [("📤 ডাটা আপলোড / রিস্টোর", f"pdbu:{i}")],
              [("♻️ সর্বশেষ ব্যাকআপ থেকে রিস্টোর", f"pdbr:{i}")],
              [("🗑 ব্যাকআপ মুছুন", f"pdbx:{i}")],
              [("◀️ প্রজেক্ট", f"p:{i}")])


@cb("pdb")
def c_data(call, a):
    p = own(call, a[0])
    if p:
        clear_state(call.from_user.id)
        show(call, data_text(p), data_kb(p))


def _data_result_note(path, status, info):
    if status == "new":
        return f"✅ ডাটা ব্যাকআপ হয়েছে ({info}টি ফাইল, {human_size(path.stat().st_size)})"
    if status == "same":
        return "ℹ️ কোনো পরিবর্তন নেই — আগের ব্যাকআপই সর্বশেষ।"
    if status == "empty":
        return "ℹ️ ব্যাকআপ করার মতো ডাটা ফাইল পাওয়া যায়নি। ডাটা <code>data/</code> ফোল্ডারে রাখুন।"
    return f"❌ ডাটা ব্যাকআপ ব্যর্থ: {esc(info)}"


@cb("pdbn")
def c_data_now(call, a):
    p = own(call, a[0])
    if not p:
        return
    ack(call, "💾 ব্যাকআপ তৈরি হচ্ছে…")

    def job():
        path, status, info = data_backup(p)
        show(call, data_text(p, _data_result_note(path, status, info)), data_kb(p))
    threading.Thread(target=job, daemon=True).start()


@cb("pdbd")
def c_data_dl(call, a):
    p = own(call, a[0])
    if not p:
        return
    ack(call, "💾 ব্যাকআপ তৈরি হচ্ছে…")

    def job():
        path, status, info = data_backup(p)
        snaps = data_snapshots(p)
        if status == "error":
            return bot.send_message(call.message.chat.id, _data_result_note(path, status, info))
        if not snaps:
            return bot.send_message(call.message.chat.id, _data_result_note(path, "empty", 0))
        last = snaps[-1]
        if last.stat().st_size > 49 * 1024 * 1024:
            return bot.send_message(call.message.chat.id, "⚠️ ডাটা ব্যাকআপ ৫০MB এর বেশি, টেলিগ্রামে পাঠানো যাচ্ছে না।")
        with open(last, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption=f"🗄 <b>{esc(p['name'])}</b> ডাটা ব্যাকআপ • {fmt_ts(time.time())}\n"
                              "রিস্টোর করতে: 🗄 ডাটা ব্যাকআপ → 📤 ডাটা আপলোড / রিস্টোর।")
    threading.Thread(target=job, daemon=True).start()


@cb("pdbu")
def c_data_up(call, a):
    p = own(call, a[0])
    if not p:
        return
    set_state(call.from_user.id, "data_up", pid=p["id"])
    show(call, head("📤", "ডাটা আপলোড", esc(p["name"]))
         + "আপনার ডাটা ফাইল (<code>.zip</code>, <code>.db</code>, <code>.json</code>, <code>.csv</code> ...) ডকুমেন্ট হিসেবে পাঠান।\n"
           "<blockquote>ZIP হলে ভেতরের <code>data/</code> ফোল্ডার ও ডাটা ফাইলগুলো রিস্টোর হবে। "
           "একক ফাইল হলে প্রজেক্টে সেভ হবে।</blockquote>\n\nবাতিল: /cancel",
         kb([("❌ বাতিল", f"pdb:{p['id']}")]))


@state("data_up")
def s_data_up(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    if not p:
        return clear_state(uid)
    if m.content_type != "document":
        return bot.send_message(m.chat.id, "❌ ডাটা ফাইলটি ডকুমেন্ট (ফাইল) হিসেবে পাঠান।")
    doc = m.document
    if doc.file_size > MAX_UPLOAD_MB * 1024 * 1024:
        return bot.send_message(m.chat.id, f"❌ ফাইল {MAX_UPLOAD_MB}MB এর বেশি।")
    name = re.sub(r"[^\w.\-]", "_", os.path.basename(doc.file_name or "data"))[:80] or "data"
    try:
        data = bot.download_file(bot.get_file(doc.file_id).file_path)
        prepare_dirs(p)
        if name.lower().endswith(".zip"):
            ok, d = fs(p, "restoredata", "", "all", MAX_UNZIP_MB * 1024 * 1024, stdin=data, timeout=300)
            if ok and not d["count"]:
                return bot.send_message(m.chat.id, "ℹ️ জিপে কোনো ডাটা ফাইল (<code>data/</code> ফোল্ডার বা .db/.json/.csv ...) পাওয়া যায়নি।")
            msg = f"✅ ডাটা রিস্টোর হয়েছে ({d['count']}টি ফাইল)।" if ok else f"❌ {esc(d)}"
        else:
            rel = name if name.lower().endswith(_DATA_EXT) else f"data/{name}"
            ok, d = fs(p, "write", rel, MAX_UPLOAD_MB * 1024 * 1024, stdin=data)
            msg = f"✅ <code>{esc(rel)}</code> সেভ হয়েছে ({human_size(len(data))})।" if ok else f"❌ {esc(d)}"
    except Exception as exc:  # noqa
        ok, msg = False, f"❌ <code>{esc(str(exc)[:300])}</code>"
    if ok:
        clear_state(uid)
        _data_bk_async(p)
        msg += "\n🔁 প্রজেক্ট রিস্টার্ট করলে নতুন ডাটা কাজ করবে।"
    bot.send_message(m.chat.id, msg, reply_markup=kb([("🗄 ডাটা ব্যাকআপ", f"pdb:{p['id']}")]))


@cb("pdbr")
def c_data_restore(call, a):
    p = own(call, a[0])
    if not p:
        return
    if not data_snapshots(p):
        return ack(call, "❌ এখনো কোনো ডাটা ব্যাকআপ নেই।", True)
    show(call, "⚠️ সর্বশেষ ব্যাকআপ থেকে ডাটা রিস্টোর করবেন? বর্তমান ডাটা ফাইলগুলো ওভাররাইট হবে।",
         kb([("✅ হ্যাঁ, রিস্টোর", f"pdbry:{p['id']}"), ("❌ না", f"pdb:{p['id']}")]))


@cb("pdbry")
def c_data_restore_yes(call, a):
    p = own(call, a[0])
    if not p:
        return
    ack(call, "♻️ রিস্টোর হচ্ছে…")
    ok, n = data_restore(p, "all")
    note = f"♻️ ডাটা রিস্টোর হয়েছে ({n}টি ফাইল)।" if ok else f"❌ {esc(n)}"
    show(call, data_text(p, note), data_kb(p))


@cb("pdbx")
def c_data_del(call, a):
    p = own(call, a[0])
    if p:
        show(call, "⚠️ এই প্রজেক্টের সব ডাটা ব্যাকআপ কপি মুছে ফেলবেন? (প্রজেক্টের বর্তমান ফাইল মুছবে না)",
             kb([("✅ হ্যাঁ, মুছুন", f"pdbxy:{p['id']}"), ("❌ না", f"pdb:{p['id']}")]))


@cb("pdbxy")
def c_data_del_yes(call, a):
    p = own(call, a[0])
    if p:
        for s in data_snapshots(p):
            s.unlink(missing_ok=True)
        ack(call, "🗑 ব্যাকআপ মুছে ফেলা হয়েছে")
        show(call, data_text(p), data_kb(p))


# ------------------------------------------------------------------ delete
@cb("pdl")
def c_delete(call, a):
    p = own(call, a[0])
    if p:
        show(call, f"⚠️ <b>{esc(p['name'])}</b> সম্পূর্ণ ডিলিট করবেন? ফাইল, লগ, এনভায়রনমেন্ট সব মুছে যাবে!",
             kb([("✅ হ্যাঁ, ডিলিট", f"pdy:{p['id']}"), ("❌ না", f"p:{p['id']}")]))


@cb("pdy")
def c_delete_yes(call, a):
    p = own(call, a[0])
    if p:
        delete_files(p)
        db_delete_project(p["id"])
        ack(call, "🗑 ডিলিট হয়েছে")
        show_projects(call)


# ==============================================================================
# 9. HANDLERS — tools (pip, env vars, GitHub, entry file)
# ==============================================================================
def _quota_ok(p, extra=0):
    # No per-plan storage quota. Physical server disk space is still finite.
    return True


PKG_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-\[\],=<>!~]*")
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")
GIT_URL = re.compile(r"https://(?:[\w.\-]+(?::[\w.\-%]+)?@)?(?:github\.com|gitlab\.com|bitbucket\.org)/"
                     r"[\w.\-]+/[\w.\-]+?(?:\.git)?/?")


# ------------------------------------------------------------------ package manager
@cb("ppm")
def c_pm(call, a):
    p = own(call, a[0])
    if not p:
        return
    pid = p["id"]
    show(call, head("📦", "Package Manager", esc(p["name"])) + "pip প্যাকেজ আপনার প্রজেক্টে আলাদাভাবে ইন্সটল হয়।",
         kb([("➕ প্যাকেজ ইন্সটল", f"ppi:{pid}"), ("📋 ইন্সটল করা", f"ppl:{pid}")],
            [("📥 requirements.txt ইন্সটল", f"ppr:{pid}")],
            [("🧹 সব লাইব্রেরি মুছুন", f"ppc:{pid}")],
            [("◀️ পেছনে", f"p:{pid}")]))


@cb("ppi")
def c_pip_ask(call, a):
    p = own(call, a[0])
    if p:
        set_state(call.from_user.id, "pip_in", pid=p["id"])
        show(call, "📦 প্যাকেজের নাম লিখুন (স্পেস দিয়ে একাধিক)\nযেমন: <code>requests aiohttp==3.9.5 pyTelegramBotAPI</code>\n\nবাতিল: /cancel")


def _pip_job(chat_id, msg_id, p, args):
    try:
        ok, out = pip_install(p, args)
    finally:
        anim_end_msg(chat_id, msg_id)
    # drop cached requirements hash so the next start re-checks nothing unexpected
    txt = (f"{'✅ ইন্সটল সফল' if ok else '❌ ইন্সটল ব্যর্থ'}\n<pre>{esc(out[-1800:])}</pre>")
    try:
        bot.edit_message_text(txt, chat_id, msg_id, reply_markup=kb([("📦 Package Manager", f"ppm:{p['id']}")]))
    except Exception:  # noqa
        bot.send_message(chat_id, txt, reply_markup=kb([("📦 Package Manager", f"ppm:{p['id']}")]))


@state("pip_in")
def s_pip(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    toks = (m.text or "").split() if m.content_type == "text" else []
    if not p or not toks or len(toks) > 15 or not all(PKG_TOKEN.fullmatch(t) for t in toks):
        return bot.send_message(m.chat.id, "❌ প্যাকেজ নাম ঠিক নয়। শুধু নাম/ভার্সন লিখুন (যেমন requests==2.31.0)।")
    if not _quota_ok(p):
        clear_state(uid)
        return bot.send_message(m.chat.id, "⛔ ডিস্ক কোটা শেষ। ফাইল মুছুন বা প্ল্যান আপগ্রেড করুন।")
    clear_state(uid)
    msg = bot.send_message(m.chat.id, f"⏳ ইন্সটল হচ্ছে: <code>{esc(' '.join(toks))}</code> …")
    anim_begin(m.chat.id, "install", mid=msg.message_id, body=f"<code>{esc(' '.join(toks))}</code>")
    threading.Thread(target=_pip_job, args=(m.chat.id, msg.message_id, p, toks), daemon=True).start()


@cb("ppr")
def c_pip_req(call, a):
    p = own(call, a[0])
    if not p:
        return
    ok, _ = fs(p, "exists", "requirements.txt")
    if not ok:
        return ack(call, "❌ requirements.txt আপলোড করা নেই", True)
    if not _quota_ok(p):
        return ack(call, "⛔ ডিস্ক কোটা শেষ", True)
    ack(call, "⏳ ইন্সটল হচ্ছে…")
    msg = bot.send_message(call.message.chat.id, "⏳ requirements.txt ইন্সটল হচ্ছে…")
    anim_begin(call.message.chat.id, "install", mid=msg.message_id, body="<code>requirements.txt</code>")
    db_update_project(p["id"], req_hash=None)
    threading.Thread(target=_pip_job, args=(call.message.chat.id, msg.message_id, p, ["-r", "requirements.txt"]),
                     daemon=True).start()


@cb("ppl")
def c_pip_list(call, a):
    p = own(call, a[0])
    if not p:
        return
    libs = libs_dir(p)
    ok, d = fs(p, "ls", "", base=libs)
    names = []
    if ok:
        for it in d["items"]:
            if it["n"].endswith(".dist-info"):
                names.append(it["n"][:-10].replace("-", " ", 1) if "-" in it["n"] else it["n"][:-10])
    txt = "\n".join(names[:80]) or "(কিছু ইন্সটল করা নেই)"
    show(call, f"📋 <b>ইন্সটল করা প্যাকেজ</b>\n<pre>{esc(txt)}</pre>", kb([("◀️ পেছনে", f"ppm:{p['id']}")]))


@cb("ppc")
def c_pip_clear(call, a):
    p = own(call, a[0])
    if p:
        shutil.rmtree(libs_dir(p), ignore_errors=True)
        prepare_dirs(p)
        db_update_project(p["id"], req_hash=None)
        ack(call, "🧹 লাইব্রেরি মুছে ফেলা হয়েছে", True)


# ------------------------------------------------------------------ environment variables
@cb("pev")
def c_env(call, a):
    p = own(call, a[0])
    if not p:
        return
    envs = db_get_envs(p["id"])
    rows = [[(f"🗑 {e['k']}", f"pevd:{p['id']}:{e['k']}")] for e in envs]
    rows.append([("➕ নতুন ভেরিয়েবল", f"peva:{p['id']}")])
    rows.append([("◀️ পেছনে", f"p:{p['id']}")])
    lst = "\n".join(f"• <code>{esc(e['k'])}</code> = ••••{esc(e['v'][-3:])}" for e in envs) or "কোনো ভেরিয়েবল নেই"
    show(call, head("🔐", "এনভায়রনমেন্ট ভেরিয়েবল", esc(p["name"])) + f"<blockquote>{lst}</blockquote>\n\n"
               f"কোডে: <code>os.getenv('KEY')</code>\nডিলিট করতে ভেরিয়েবলের বাটনে চাপুন।", kb(*rows))


@cb("peva")
def c_env_add(call, a):
    p = own(call, a[0])
    if p:
        if len(db_get_envs(p["id"])) >= 50:
            return ack(call, "সর্বোচ্চ ৫০টি ভেরিয়েবল", True)
        set_state(call.from_user.id, "env_add", pid=p["id"])
        show(call, "🔐 <code>KEY=VALUE</code> ফরম্যাটে পাঠান\nযেমন: <code>BOT_TOKEN=123:ABC...</code>\n\n"
                   "⚠️ মেসেজটি সেভ হলে আমি ডিলিট করে দেব।\nবাতিল: /cancel")


@state("env_add")
def s_env_add(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    text = (m.text or "") if m.content_type == "text" else ""
    if "=" not in text or not p:
        return bot.send_message(m.chat.id, "❌ ফরম্যাট: KEY=VALUE")
    k, v = text.split("=", 1)
    k, v = k.strip(), v.strip()
    if not ENV_KEY.fullmatch(k) or not v or len(v) > 2000:
        return bot.send_message(m.chat.id, "❌ KEY শুধু ইংরেজি অক্ষর/সংখ্যা/_ (সর্বোচ্চ ৩২), VALUE খালি হবে না।")
    db_set_env(p["id"], k, v)
    clear_state(uid)
    try:
        bot.delete_message(m.chat.id, m.message_id)
    except Exception:  # noqa
        pass
    bot.send_message(m.chat.id, f"✅ <code>{esc(k)}</code> সেভ হয়েছে। রিস্টার্ট দিলে কার্যকর হবে।",
                     reply_markup=kb([("🔐 ভেরিয়েবল তালিকা", f"pev:{p['id']}")]))


@cb("pevd")
def c_env_del(call, a):
    p = own(call, a[0])
    if p and len(a) > 1:
        db_del_env(p["id"], a[1])
        ack(call, "🗑 মুছে ফেলা হয়েছে")
        c_env(call, [str(p["id"])])


# ------------------------------------------------------------------ GitHub sync
@cb("pg")
def c_git(call, a):
    p = own(call, a[0])
    if not p:
        return
    url = p["github_url"]
    if not url:
        set_state(call.from_user.id, "git_url", pid=p["id"])
        return show(call, "🐙 <b>গিটহাব সিঙ্ক</b>\nরিপোর URL পাঠান:\n<code>https://github.com/user/repo</code>\n"
                          "প্রাইভেট রিপোর জন্য: <code>https://user:TOKEN@github.com/user/repo</code>\n\nবাতিল: /cancel")
    shown = re.sub(r"https://[^@\s]+@", "https://***@", url)
    show(call, f"🐙 <b>গিটহাব সিঙ্ক</b>\n🔗 <code>{esc(shown)}</code>\n\nসিঙ্ক করলে রিপোর লেটেস্ট ফাইল এই প্রজেক্টে আসবে "
               f"(একই নামের ফাইল ওভাররাইট হবে)।",
         kb([("🔄 এখনই সিঙ্ক", f"pgs:{p['id']}")], [("✏️ URL পরিবর্তন", f"pgu:{p['id']}")], [("◀️ পেছনে", f"p:{p['id']}")]))


@cb("pgu")
def c_git_url(call, a):
    p = own(call, a[0])
    if p:
        set_state(call.from_user.id, "git_url", pid=p["id"])
        show(call, "🐙 নতুন রিপো URL পাঠান:\n\nবাতিল: /cancel")


@state("git_url")
def s_git_url(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    url = (m.text or "").strip() if m.content_type == "text" else ""
    if not p or not GIT_URL.fullmatch(url):
        return bot.send_message(m.chat.id, "❌ শুধু https://github.com / gitlab.com / bitbucket.org এর রিপো URL দিন।")
    db_update_project(p["id"], github_url=url)
    clear_state(uid)
    try:
        bot.delete_message(m.chat.id, m.message_id)
    except Exception:  # noqa
        pass
    bot.send_message(m.chat.id, "✅ URL সেভ হয়েছে।", reply_markup=kb([("🔄 এখনই সিঙ্ক", f"pgs:{p['id']}")]))


@cb("pgs")
def c_git_sync(call, a):
    p = own(call, a[0])
    if not p or not p["github_url"]:
        return
    if not _quota_ok(p):
        return ack(call, "⛔ ডিস্ক কোটা শেষ", True)
    ack(call, "⏳ সিঙ্ক হচ্ছে…")
    chat = call.message.chat.id

    def job():
        with work(chat, "git"):
            ok, out = git_sync(p, p["github_url"])
        extra = ""
        if ok and not db_get_project(p["id"])["entry"]:
            e = detect_entry(p)
            if e:
                db_update_project(p["id"], entry=e)
                extra = f"\n📝 এন্ট্রি ফাইল সেট: <code>{esc(e)}</code>"
        bot.send_message(chat, ("✅ সিঙ্ক সম্পন্ন!" if ok else "❌ সিঙ্ক ব্যর্থ:") + f"\n<pre>{esc(out)}</pre>" + extra,
                         reply_markup=kb([("🎛 প্যানেল", f"p:{p['id']}")]))
    threading.Thread(target=job, daemon=True).start()


# ------------------------------------------------------------------ entry file
@cb("pen")
def c_entry(call, a):
    p = own(call, a[0])
    if not p:
        return
    ok, d = fs(p, "find_py")
    files = d["files"] if ok else []
    if not files:
        return ack(call, "❌ কোনো .py ফাইল নেই। আগে ফাইল আপলোড করুন।", True)
    CACHE[(call.from_user.id, "entries")] = files
    rows = [[(("✅ " if f == p["entry"] else "📄 ") + f[:40], f"pse:{p['id']}:{i}")] for i, f in enumerate(files[:30])]
    rows.append([("◀️ পেছনে", f"p:{p['id']}")])
    show(call, "📝 <b>কোন ফাইলটি চালু হবে?</b>\nবর্তমান: <code>" + esc(p["entry"] or "—") + "</code>", kb(*rows))


@cb("pse")
def c_entry_set(call, a):
    p = own(call, a[0])
    files = CACHE.get((call.from_user.id, "entries"), [])
    if p and len(a) > 1 and a[1].isdigit() and int(a[1]) < len(files):
        db_update_project(p["id"], entry=files[int(a[1])])
        ack(call, "✅ এন্ট্রি ফাইল সেট হয়েছে")
        show_panel(call, p)


# ==============================================================================
# 10. HANDLERS — file manager & uploads
# ==============================================================================
PER_PAGE = 14
TEXT_EXT = (".py", ".txt", ".json", ".md", ".env", ".cfg", ".ini", ".yml", ".yaml", ".toml", ".csv", ".log", ".html",
            ".js", ".sh", ".sql", ".xml", ".conf")


def _fm(uid):
    return CACHE.get((uid, "fm"))


def _join(rel, name):
    return posixpath.normpath(posixpath.join(rel or "", name)).lstrip("./") if rel or name else ""


def render(obj, note=""):
    uid = obj.from_user.id
    s = _fm(uid)
    p = owned_by(uid, s["pid"]) if s else None
    if not p:
        return show(obj, "❌ সেশন শেষ। আবার ফাইল ম্যানেজার খুলুন।")
    ok, d = fs(p, "ls", s["rel"])
    if not ok:
        s["rel"] = ""
        ok, d = fs(p, "ls", "")
        if not ok:
            return show(obj, f"❌ {esc(d)}", kb([("◀️ পেছনে", f"p:{p['id']}")]))
    items = d["items"]
    s["items"] = items
    pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE)
    s["page"] = min(s.get("page", 0), pages - 1)
    chunk = items[s["page"] * PER_PAGE:(s["page"] + 1) * PER_PAGE]
    rows = []
    for idx, it in enumerate(chunk, start=s["page"] * PER_PAGE):
        label = ("📁 " + it["n"][:30]) if it["d"] else f"📄 {it['n'][:24]}  ({human_size(it['s'])})"
        rows.append([(label, f"fo:{idx}")])
    nav = []
    if s["page"] > 0:
        nav.append(("⬅️", f"fpg:{s['page'] - 1}"))
    if s["page"] < pages - 1:
        nav.append(("➡️", f"fpg:{s['page'] + 1}"))
    if nav:
        rows.append(nav)
    rows += [[("⬆️ উপরে", "fu"), ("📤 আপলোড", "fup")],
             [("➕ ফোল্ডার", "fnew"), ("🔄 রিফ্রেশ", "frf")],
             [("◀️ প্রজেক্ট", f"p:{p['id']}")]]
    used = human_size(disk_usage(p))
    storage_line = f"\n💽 ব্যবহৃত: {used}" if (isinstance(obj, types.CallbackQuery) and is_admin(obj.from_user.id)) else ""
    show(obj, (head("📁", "ফাইল ম্যানেজার", esc(p["name"]))
               + f"<blockquote>📍 <code>/{esc(s['rel'])}</code>{storage_line}</blockquote>\n"
               f"{note}" + ("\n(ফোল্ডার খালি)" if not items else "")), kb(*rows))


@menu(M_FILES)
def m_files(m):
    projs = db_user_projects(m.from_user.id)
    if not projs:
        return show(m, "📁 আগে একটি প্রজেক্ট বানান: 🎛 কন্ট্রোল প্যানেল → ➕ নতুন প্রজেক্ট")
    show(m, "📁 <b>কোন প্রজেক্টের ফাইল দেখবেন?</b>", kb(*[[(f"📦 {p['name']}", f"fm:{p['id']}")] for p in projs]))


@cb("fm")
def c_fm(call, a):
    p = own(call, a[0])
    if p:
        clear_state(call.from_user.id)
        CACHE[(call.from_user.id, "fm")] = {"pid": p["id"], "rel": "", "items": [], "page": 0}
        render(call)


@cb("frf")
def c_refresh(call, a):
    render(call)


@cb("fpg")
def c_page(call, a):
    s = _fm(call.from_user.id)
    if s and a and a[0].isdigit():
        s["page"] = int(a[0])
        render(call)


@cb("fu")
def c_fm_up(call, a):
    s = _fm(call.from_user.id)
    if s:
        s["rel"] = posixpath.dirname(s["rel"]) if s["rel"] else ""
        s["page"] = 0
        render(call)


def _item(call, a):
    s = _fm(call.from_user.id)
    if not s or not a or not a[0].isdigit() or int(a[0]) >= len(s["items"]):
        ack(call, "সেশন শেষ, রিফ্রেশ করুন", True)
        return None, None, None
    p = owned_by(call.from_user.id, s["pid"])
    it = s["items"][int(a[0])]
    return s, p, it


@cb("fo")
def c_open(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    rel = _join(s["rel"], it["n"])
    if it["d"]:
        s["rel"], s["page"] = rel, 0
        return render(call)
    i = a[0]
    rows = [[("👁 দেখুন", f"fv:{i}"), ("📥 ডাউনলোড", f"fdl:{i}")], [("✏️ এডিট", f"fe:{i}"), ("🗑 ডিলিট", f"frm:{i}")]]
    if it["n"].endswith(".py"):
        rows.append([("📝 এন্ট্রি ফাইল করুন", f"fen:{i}")])
    if it["n"].lower().endswith(".zip"):
        rows.append([("📦 আনজিপ", f"fz:{i}")])
    rows.append([("◀️ ফোল্ডারে ফিরুন", "frf")])
    show(call, f"📄 <b>{esc(it['n'])}</b>\n📏 {human_size(it['s'])}\n📍 <code>/{esc(rel)}</code>", kb(*rows))


@cb("fv")
def c_view(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    ok, data = fs(p, "read", _join(s["rel"], it["n"]), 3000)
    if not ok:
        return ack(call, str(data)[:150], True)
    if b"\x00" in data[:500]:
        return ack(call, "বাইনারি ফাইল — ডাউনলোড করুন", True)
    txt = data[:3000].decode(errors="replace")
    show(call, f"👁 <b>{esc(it['n'])}</b>{' (প্রথম অংশ)' if len(data) > 3000 else ''}\n<pre>{esc(txt)[:3300]}</pre>",
         kb([("✏️ এডিট", f"fe:{a[0]}"), ("📥 ডাউনলোড", f"fdl:{a[0]}")], [("◀️ পেছনে", "frf")]))


@cb("fdl")
def c_download(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    if it["s"] > 49 * 1024 * 1024:
        return ack(call, "ফাইল ৫০MB এর বেশি", True)
    ok, data = fs(p, "read", _join(s["rel"], it["n"]), 49 * 1024 * 1024, timeout=180)
    if not ok:
        return ack(call, str(data)[:150], True)
    bot.send_document(call.message.chat.id, (it["n"], data))


@cb("frm")
def c_rm_ask(call, a):
    s, p, it = _item(call, a)
    if it:
        show(call, f"🗑 <b>{esc(it['n'])}</b> ডিলিট করবেন?", kb([("✅ হ্যাঁ", f"fry:{a[0]}"), ("❌ না", "frf")]))


@cb("fry")
def c_rm(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    ok, err = fs(p, "rm", _join(s["rel"], it["n"]))
    render(call, "🗑 ডিলিট হয়েছে" if ok else f"❌ {esc(err)}")


@cb("fen")
def c_file_entry(call, a):
    s, p, it = _item(call, a)
    if it:
        db_update_project(p["id"], entry=_join(s["rel"], it["n"]))
        ack(call, "✅ এন্ট্রি ফাইল সেট হয়েছে", True)


@cb("fz")
def c_unzip(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    ok, d = fs(p, "unzipfile", _join(s["rel"], it["n"]), MAX_UNZIP_MB * 1024 * 1024, s["rel"],
                         timeout=300)
    render(call, f"✅ আনজিপ হয়েছে ({d['count']}টি ফাইল)" if ok else f"❌ {esc(d)}")


@cb("fnew")
def c_newdir(call, a):
    s = _fm(call.from_user.id)
    if s:
        set_state(call.from_user.id, "fm_mkdir", rel=s["rel"], pid=s["pid"])
        show(call, "📁 নতুন ফোল্ডারের নাম লিখুন:\n\nবাতিল: /cancel")


@state("fm_mkdir")
def s_mkdir(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    name = (m.text or "").strip() if m.content_type == "text" else ""
    if not p or not re.fullmatch(r"[\w.\-]{1,40}", name):
        return bot.send_message(m.chat.id, "❌ নাম ঠিক নয় (ইংরেজি/সংখ্যা/._-)।")
    ok, err = fs(p, "mkdir", _join(st["rel"], name))
    clear_state(uid)
    bot.send_message(m.chat.id, "✅ ফোল্ডার তৈরি হয়েছে" if ok else f"❌ {esc(err)}",
                     reply_markup=kb([("📁 ফাইল ম্যানেজার", f"fm:{p['id']}")]))


# ------------------------------------------------------------------ edit text file
@cb("fe")
def c_edit(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    if it["s"] > 100_000 or not it["n"].lower().endswith(TEXT_EXT):
        return ack(call, "শুধু ১০০KB এর ছোট টেক্সট ফাইল এডিট করা যায়", True)
    set_state(call.from_user.id, "fm_edit", pid=p["id"], rel=_join(s["rel"], it["n"]))
    show(call, f"✏️ <b>{esc(it['n'])}</b>\nনতুন সম্পূর্ণ কনটেন্ট মেসেজে পাঠান (পুরনো কনটেন্ট রিপ্লেস হবে)।\n"
               f"বা একটি ফাইল পাঠান যেটা এটাকে রিপ্লেস করবে।\n\nবাতিল: /cancel")


@state("fm_edit")
def s_edit(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    if not p:
        return
    if m.content_type == "text":
        data = m.text.encode()
    elif m.content_type == "document" and m.document.file_size <= 100_000:
        data = bot.download_file(bot.get_file(m.document.file_id).file_path)
    else:
        return bot.send_message(m.chat.id, "❌ টেক্সট বা ছোট ফাইল পাঠান।")
    ok, err = fs(p, "write", st["rel"], 100_000, stdin=data)
    clear_state(uid)
    bot.send_message(m.chat.id, "✅ ফাইল আপডেট হয়েছে। রিস্টার্ট দিলে কার্যকর হবে।" if ok else f"❌ {esc(err)}",
                     reply_markup=kb([("🎛 প্যানেল", f"p:{p['id']}")]))


# ------------------------------------------------------------------ upload
@cb("fup")
def c_upload(call, a):
    s = _fm(call.from_user.id)
    if s:
        set_state(call.from_user.id, "upload", pid=s["pid"], rel=s["rel"])
        show(call, f"📤 <b>আপলোড মোড</b>\n📍 <code>/{esc(s['rel'])}</code>\n\n.py / requirements.txt / যেকোনো ফাইল বা "
                   f".zip পাঠান (সর্বোচ্চ {MAX_UPLOAD_MB}MB)। ZIP অটো আনজিপ হবে।",
             kb([("✅ আপলোড শেষ", f"fupd:{s['pid']}")]))


@cb("fupd")
def c_upload_done(call, a):
    p = own(call, a[0])
    if not p:
        return
    clear_state(call.from_user.id)
    if not db_get_project(p["id"])["entry"]:
        e = detect_entry(p)
        if e:
            db_update_project(p["id"], entry=e)
    show_panel(call, p)


@state("upload")
def s_upload(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    if not p:
        return clear_state(uid)
    if m.content_type != "document":
        return bot.send_message(m.chat.id, "📤 ফাইল (document) পাঠান। শেষ হলে ✅ আপলোড শেষ চাপুন।")
    doc = m.document
    if doc.file_size > MAX_UPLOAD_MB * 1024 * 1024:
        return bot.send_message(m.chat.id, f"❌ ফাইল {MAX_UPLOAD_MB}MB এর বেশি।")
    if not _quota_ok(p, doc.file_size):
        return bot.send_message(m.chat.id, "⛔ ডিস্ক কোটা শেষ। ফাইল মুছুন বা প্ল্যান আপগ্রেড করুন।")
    name = os.path.basename(doc.file_name or "file")
    name = re.sub(r"[^\w.\-]", "_", name)[:80] or "file"
    # Show an immediate status message while the file is being downloaded/saved.
    status = bot.send_message(
        m.chat.id,
        f"⏳ <b>ফাইল আপলোড হচ্ছে...</b>\n📄 <code>{esc(name)}</code>\n"
        f"📦 সাইজ: {human_size(doc.file_size)}\nঅনুগ্রহ করে অপেক্ষা করুন।"
    )
    _up_anim = anim_begin(m.chat.id, "upload", mid=status.message_id,
                          body=f"📄 <code>{esc(name)}</code>\n📦 সাইজ: {human_size(doc.file_size)}")
    try:
        data = bot.download_file(bot.get_file(doc.file_id).file_path)
        if name.lower().endswith(".zip"):
            ok, d = fs(p, "unzip", st["rel"], MAX_UNZIP_MB * 1024 * 1024, stdin=data, timeout=300)
            msg = f"✅ <b>{esc(name)}</b> আনজিপ হয়েছে ({d['count']}টি ফাইল)" if ok else f"❌ {esc(d)}"
        else:
            ok, d = fs(p, "write", _join(st["rel"], name), MAX_UPLOAD_MB * 1024 * 1024, stdin=data)
            msg = f"✅ <b>{esc(name)}</b> সেভ হয়েছে ({human_size(len(data))})" if ok else f"❌ {esc(d)}"
        if ok and not db_get_project(p["id"])["entry"]:
            e = detect_entry(p)
            if e:
                db_update_project(p["id"], entry=e)
                msg += f"\n📝 এন্ট্রি ফাইল অটো-সেট: <code>{esc(e)}</code>"
        final_text = msg + "\n\nআরও পাঠান বা শেষ করুন 👇"
    except Exception as exc:
        final_text = f"❌ <b>ফাইল আপলোড ব্যর্থ হয়েছে</b>\n<code>{esc(str(exc)[:500])}</code>"
    anim_end(_up_anim)
    try:
        bot.edit_message_text(final_text, m.chat.id, status.message_id,
                              parse_mode="HTML", reply_markup=kb([("✅ আপলোড শেষ", f"fupd:{p['id']}")]))
    except Exception:
        bot.send_message(m.chat.id, final_text, parse_mode="HTML",
                         reply_markup=kb([("✅ আপলোড শেষ", f"fupd:{p['id']}")]))


# ==============================================================================
# 11. HANDLERS — plans & payments
# ==============================================================================
METHODS = {"bkash": "bKash", "nagad": "Nagad"}


def show_plans(obj):
    uid = obj.from_user.id
    u = db_get_user(uid)
    st = db_plan_state(u)
    plans = db_list_plans(active_only=True, paid_only=True)
    txt = (head("💎", "প্ল্যান সমূহ", "আপনার প্রয়োজন অনুযায়ী সেরাটি বেছে নিন")
           + f"<blockquote>👑 আপনার প্ল্যান: <b>{st['plan']['name']}</b> • ⏳ {plan_left(st)}\n"
             f"💰 ব্যালেন্স: <b>৳{u['balance']:g}</b></blockquote>\n\n")
    txt += "\n\n".join(plan_card(p) for p in plans) if plans else "এখন কোনো প্ল্যান চালু নেই।"
    rows = [[(f"{p['name']} — ৳{p['price']:g}", f"buy:{p['id']}")] for p in plans]
    if vp_enabled() and not vp_claimed(uid):
        vp = vp_plan()
        rows.append([(f"🎁 ফ্রিতে নিন — {vp['name']} ({vp_days()} দিন)", "vpf")])
    show(obj, txt, kb(*rows) if rows else None)


@menu(M_UPGRADE)
def m_upgrade(m):
    show_plans(m)


@cb("up")
def c_up(call, a):
    show_plans(call)


@cb("buy")
def c_buy(call, a):
    plan = db_get_plan(a[0]) if a else None
    if not plan or not plan["active"] or plan["id"] == "free":
        return ack(call, "প্ল্যান পাওয়া যায়নি", True)
    bal = db_get_user(call.from_user.id)["balance"]
    methods = []
    if db_get_setting("bkash_on") != "0":
        methods.append(("💗 bKash", f"pay:{plan['id']}:bkash"))
    if db_get_setting("nagad_on") != "0":
        methods.append(("🟠 Nagad", f"pay:{plan['id']}:nagad"))
    rows = ([methods] if methods else []) + [[(f"💰 ব্যালেন্স (৳{bal:g})", f"pay:{plan['id']}:bal")],
                                            [("◀️ পেছনে", "up")]]
    show(call, head("🛒", "ক্রয় নিশ্চিত করুন", "শেষ ধাপ — পেমেন্ট মাধ্যম বেছে নিন")
               + f"{plan_card(plan)}\n\nপেমেন্ট মাধ্যম বেছে নিন 👇", kb(*rows))


@cb("pay")
def c_pay(call, a):
    uid = call.from_user.id
    plan = db_get_plan(a[0]) if len(a) > 1 else None
    if not plan or plan["id"] == "free":
        return ack(call, "প্ল্যান পাওয়া যায়নি", True)
    method = a[1]
    if method == "bal":
        bal = db_get_user(uid)["balance"]
        if bal < plan["price"]:
            return ack(call, f"❌ ব্যালেন্স কম! দরকার ৳{plan['price']:g}, আছে ৳{bal:g}।\nরেফার করে আর্ন করুন।", True)
        return show(call, f"💰 ব্যালেন্স থেকে <b>৳{plan['price']:g}</b> কেটে <b>{plan['name']}</b> কিনবেন?",
                    kb([("✅ হ্যাঁ, কিনুন", f"pbal:{plan['id']}"), ("❌ না", f"buy:{plan['id']}")]))
    if method not in METHODS:
        return
    if db_get_setting(f"{method}_on") == "0":
        return ack(call, "⛔ এই পেমেন্ট মাধ্যম এখন বন্ধ আছে। অন্যটি বেছে নিন।", True)
    if db_user_pending_count(uid) >= 3:
        return ack(call, "⏳ আপনার ৩টি পেমেন্ট এখনো যাচাই বাকি। অপেক্ষা করুন।", True)
    number = db_get_setting(method)
    note = db_get_setting("payment_note").strip()
    note_line = f"\n📝 {esc(note)}" if note else ""
    set_state(uid, "trx", plan=plan["id"], method=method)
    show(call, (head("💳", f"{METHODS[method]} পেমেন্ট", "নিরাপদ ম্যানুয়াল পেমেন্ট — ৩টি সহজ ধাপ")
                + f"<b>1️⃣ Send Money করুন</b>\n"
                  f"<blockquote>{METHODS[method]} অ্যাপ খুলে <b>Send Money</b> করুন\n"
                  f"📱 নম্বর: <code>{esc(number)}</code>\n"
                  f"💵 পরিমাণ: <b>৳{plan['price']:g}</b></blockquote>\n"
                  f"<b>2️⃣ TrxID কপি করুন</b>\nসেন্ড করার পর <b>Transaction ID (TrxID)</b> কপি করুন।\n\n"
                  f"<b>3️⃣ TrxID পাঠান</b>\nএখানে TrxID পাঠান 👇 (অ্যাডমিন যাচাই করে অ্যাপ্রুভ করলে প্ল্যান চালু হবে)\n\n"
                  f"⚠️ ঠিক ৳{plan['price']:g} পাঠাবেন। ভুল/ভুয়া TrxID দিলে একাউন্ট ব্যান হতে পারে।{note_line}\nবাতিল: /cancel"),
         kb([("◀️ পেছনে", f"buy:{plan['id']}")]))


@cb("pbal")
def c_pay_balance(call, a):
    uid = call.from_user.id
    plan = db_get_plan(a[0]) if a else None
    if not plan or plan["id"] == "free" or not plan["active"]:
        return ack(call, "প্ল্যান পাওয়া যায়নি", True)
    if not db_charge_balance(uid, plan["price"]):
        return ack(call, "❌ ব্যালেন্স কম!", True)
    exp = db_activate_plan(uid, plan["id"])
    show(call, head("🎉", f"{plan['name']} সক্রিয় হয়েছে!", "পেমেন্ট সফল — উপভোগ করুন")
               + f"<blockquote>⏳ মেয়াদ: {fmt_ts(exp)}</blockquote>\n\n🎛 কন্ট্রোল প্যানেল থেকে প্রজেক্ট চালু করুন।")


@state("trx")
def s_trx(m, st):
    uid = m.from_user.id
    trx = (m.text or "").strip().upper() if m.content_type == "text" else ""
    plan = db_get_plan(st["plan"])
    if not plan:
        return clear_state(uid)
    if not re.fullmatch(r"[A-Z0-9]{6,20}", trx):
        return bot.send_message(m.chat.id, "❌ TrxID ঠিক নয় (৬-২০ অক্ষর/সংখ্যা)। আবার পাঠান।")
    if db_trx_exists(trx):
        return bot.send_message(m.chat.id, "❌ এই TrxID আগেই জমা দেওয়া হয়েছে।")
    if db_user_pending_count(uid) >= 3:
        clear_state(uid)
        return bot.send_message(m.chat.id, "⏳ আগের পেমেন্ট যাচাই বাকি।")
    pid = db_add_payment(uid, plan["id"], st["method"], plan["price"], trx)
    clear_state(uid)
    bot.send_message(m.chat.id, head("✅", "পেমেন্ট জমা হয়েছে!", "যাচাইয়ের অপেক্ষায়")
                     + f"<blockquote>🧾 TrxID: <code>{esc(trx)}</code></blockquote>\n"
                       f"অ্যাডমিন যাচাই করলেই প্ল্যান সক্রিয় হবে (সাধারণত ৫-৩০ মিনিট)। আমি আপনাকে জানিয়ে দেব।")
    u = db_get_user(uid)
    notify_admins(payment_text(db_get_payment(pid), u),
                  kb([("✅ Approve", f"payok:{pid}"), ("❌ Reject", f"payno:{pid}")]))


def payment_text(pay, u):
    plan = db_get_plan(pay["plan_id"])
    uname = f"@{u['username']}" if u.get("username") else "—"
    return (head("💳", f"নতুন পেমেন্ট #{pay['id']}", "অ্যাপ্রুভালের অপেক্ষায়")
            + f"👤 {esc(u['first_name'] or '')} ({uname}) <code>{u['id']}</code>\n"
              f"📦 প্ল্যান: {plan['name'] if plan else pay['plan_id']}\n💵 পরিমাণ: <b>৳{pay['amount']:g}</b>\n"
              f"📲 মাধ্যম: {METHODS.get(pay['method'], pay['method'])}\n🧾 TrxID: <code>{esc(pay['trx_id'])}</code>\n"
              f"🕒 {fmt_ts(pay['created_at'])}\n\n⚠️ {METHODS.get(pay['method'])} অ্যাপে TrxID ও পরিমাণ মিলিয়ে নিন।")


@cb("payok")
def c_approve(call, a):
    if not is_admin(call.from_user.id):
        return ack(call, "❌ অনুমতি নেই", True)
    pid = int(a[0])
    cur = db_x("UPDATE payments SET status='approved', decided_by=? WHERE id=? AND status='pending'",
               (call.from_user.id, pid))
    if cur.rowcount != 1:
        return ack(call, "ইতোমধ্যে প্রসেস করা হয়েছে", True)
    pay = db_get_payment(pid)
    exp = db_activate_plan(pay["user_id"], pay["plan_id"])
    plan = db_get_plan(pay["plan_id"])
    notify(pay["user_id"], head("🎉", "পেমেন্ট অ্যাপ্রুভ হয়েছে!", "ধন্যবাদ — আপনার প্ল্যান এখন সক্রিয়")
           + f"<blockquote>👑 প্ল্যান: <b>{plan['name']}</b>\n⏳ মেয়াদ: {fmt_ts(exp)}</blockquote>\n\n"
             f"🎛 কন্ট্রোল প্যানেল থেকে প্রজেক্ট চালু করুন।")
    show(call, call.message.html_text + f"\n\n✅ <b>Approved</b> by {call.from_user.id}")


@cb("payno")
def c_reject(call, a):
    if not is_admin(call.from_user.id):
        return ack(call, "❌ অনুমতি নেই", True)
    pid = int(a[0])
    cur = db_x("UPDATE payments SET status='rejected', decided_by=? WHERE id=? AND status='pending'",
               (call.from_user.id, pid))
    if cur.rowcount != 1:
        return ack(call, "ইতোমধ্যে প্রসেস করা হয়েছে", True)
    pay = db_get_payment(pid)
    notify(pay["user_id"], head("❌", "পেমেন্ট বাতিল হয়েছে", "যাচাই সম্পন্ন হয়নি")
           + f"<blockquote>🧾 TrxID: <code>{esc(pay['trx_id'])}</code></blockquote>\n"
             f"ভুল মনে হলে 🛰 সাপোর্ট হাবে যোগাযোগ করুন।")
    show(call, call.message.html_text + f"\n\n❌ <b>Rejected</b> by {call.from_user.id}")


# ==============================================================================
# 11b. VPLINK VERIFICATION — free plan after the user passes a VPLink short link
# ==============================================================================
_vp_pending = {}     # uid -> token (user opened a verify link before joining the channels)
_vp_last = {}        # uid -> last time a verify link was requested (anti-spam)


def vp_plan():
    pid = (db_get_setting("vplink_plan") or "").strip()
    p = db_get_plan(pid) if pid else None
    return p if p and p["active"] and p["id"] != "free" else None


def vp_days():
    try:
        return max(1, int(db_get_setting("vplink_days") or 1))
    except ValueError:
        return 1


def vp_enabled():
    return db_get_setting("vplink_on") == "1" and bool(VPLINK_API_KEY) and vp_plan() is not None


def vp_claimed(uid):
    """True if this Telegram user already took the VPLink free plan once (permanent — never expires)."""
    return db_q1("SELECT 1 FROM vp_claims WHERE user_id=?", (uid,)) is not None


def vp_blocked_reason(uid):
    """Already claimed once -> never again. Active paid plan -> must not be switched/downgraded."""
    if vp_claimed(uid):
        return "⛔ আপনি ইতিমধ্যে VPLink ফ্রি প্ল্যান একবার নিয়েছেন। এটা আর নেওয়া যাবে না — পেইড প্ল্যান দেখুন।"
    st = db_plan_state(db_get_user(uid))
    if st["paid"] and st["active"]:
        return f"✅ আপনার {st['plan']['name']} প্ল্যান আগে থেকেই সক্রিয় ({plan_left(st)} বাকি)। মেয়াদ শেষ হলে আবার নিতে পারবেন।"
    return None


def vplink_shorten(long_url):
    """Returns the VPLink short URL, or None on any failure."""
    # Send the destination exactly like the VPLink dashboard example (raw, NOT percent-encoded):
    #   https://vplink.in/api?api=KEY&url=https://t.me/bot?start=vp_xxx
    # (an encoded url= value made the final redirect page answer "404 Not Found")
    q = f"api={urllib.parse.quote(VPLINK_API_KEY, safe='')}&url={urllib.parse.quote(long_url, safe=':/?=_-')}"
    try:
        req = urllib.request.Request(f"{VPLINK_API_URL}?{q}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa
        log.warning("vplink api failed: %s", e)
        return None
    if not isinstance(data, dict) or str(data.get("status", "")).lower() not in ("success", "ok", "true", "1"):
        log.warning("vplink api error: %s", str(data)[:200])
        return None
    short = data.get("shortenedUrl") or data.get("shortened_url") or data.get("short_url") or data.get("url")
    if isinstance(short, str) and short.startswith("http"):
        log.info("vplink short link created: %s  ->  %s", short, long_url)
        return short
    return None


@cb("vpf")
def c_vp_free(call, a):
    uid = call.from_user.id
    if not vp_enabled():
        return ack(call, "⛔ এই অপশন এখন বন্ধ আছে।", True)
    why = vp_blocked_reason(uid)
    if why:
        return ack(call, why, True)
    now = time.time()
    if now - _vp_last.get(uid, 0) < 8:
        return ack(call, "⏳ একটু অপেক্ষা করে আবার চাপুন।", True)
    _vp_last[uid] = now
    plan, days = vp_plan(), vp_days()
    token = secrets.token_urlsafe(9)
    short = vplink_shorten(f"https://t.me/{bot_username()}?start=vp_{token}")
    if not short:
        return ack(call, "⚠️ লিংক বানানো যায়নি। একটু পরে আবার চেষ্টা করুন।", True)
    db_x("UPDATE vp_tokens SET used=1 WHERE user_id=? AND used=0", (uid,))     # only the newest link works
    db_x("INSERT INTO vp_tokens(token,user_id,plan_id,days,created_at) VALUES(?,?,?,?,?)",
         (token, uid, plan["id"], days, now))
    show(call, head("🎁", "ফ্রি প্ল্যান — লিংক ভেরিফাই", "ভেরিফাই সম্পন্ন হলেই প্ল্যান সক্রিয় হবে")
               + f"{plan_card({**plan, 'days': days, 'price': 0})}\n\n"
                 "<b>কীভাবে নেবেন?</b>\n"
                 "1️⃣ নিচের <b>🔗 ভেরিফাই লিংক</b> বাটনে চাপুন\n"
                 "2️⃣ লিংকের পেজের ধাপগুলো শেষ করুন (পেজ বন্ধ না করে)\n"
                 "3️⃣ শেষে লাস্ট পেজে <b>GETLINK</b> চাপুন — প্ল্যান সাথে সাথে চালু হয়ে যাবে\n\n"
                 "<blockquote>⚠️ লিংক ৩০ মিনিট চলবে এবং শুধু আপনার একাউন্টে একবারই কাজ করবে। "
                 "লিংকের ধাপ না করে সরাসরি এলে প্ল্যান পাবেন না।</blockquote>",
         kb([("🔗 ভেরিফাই লিংক", short)], [("◀️ পেছনে", "up")]))


def redeem_vplink(uid, chat_id, token):
    def fail(msg):
        bot.send_message(chat_id, head("❌", "ভেরিফিকেশন হয়নি", "প্ল্যান সক্রিয় করা যায়নি") + msg,
                         reply_markup=kb([("🎁 নতুন লিংক নিন", "up")]))

    row = db_q1("SELECT * FROM vp_tokens WHERE token=?", (token,))
    if not row or row["user_id"] != uid:
        return fail("লিংকটি ঠিক নয় বা অন্য একাউন্টের। নিজের একাউন্ট থেকে নতুন লিংক নিন।")
    if row["used"]:
        return fail("এই লিংক আগেই ব্যবহার হয়েছে বা নতুন লিংক নেওয়ায় বাতিল হয়েছে।")
    age = time.time() - row["created_at"]
    if age > VPLINK_TOKEN_TTL:
        db_x("UPDATE vp_tokens SET used=1 WHERE token=?", (token,))
        return fail("লিংকের মেয়াদ (৩০ মিনিট) শেষ। নতুন লিংক নিন।")
    if age < VPLINK_MIN_SECONDS:
        db_x("UPDATE vp_tokens SET used=1 WHERE token=?", (token,))
        return fail("আপনি লিংকের ধাপগুলো সম্পূর্ণ করেননি। নতুন লিংক নিয়ে সব ধাপ শেষ করুন।")
    why = vp_blocked_reason(uid)
    if why:
        return bot.send_message(chat_id, why)
    plan = db_get_plan(row["plan_id"])
    if not plan or not plan["active"]:
        return fail("প্ল্যানটি এখন আর পাওয়া যাচ্ছে না। অ্যাডমিনের সাথে যোগাযোগ করুন।")
    if db_x("UPDATE vp_tokens SET used=1 WHERE token=? AND used=0", (token,)).rowcount != 1:
        return fail("এই লিংক আগেই ব্যবহার হয়েছে।")
    # permanent one-time claim (atomic: a second attempt inserts 0 rows and is refused)
    if db_x("INSERT OR IGNORE INTO vp_claims(user_id,plan_id,claimed_at) VALUES(?,?,?)",
            (uid, plan["id"], time.time())).rowcount != 1:
        return fail("আপনি ইতিমধ্যে VPLink ফ্রি প্ল্যান একবার নিয়েছেন।")
    try:
        exp = db_activate_plan(uid, plan["id"], row["days"])
    except Exception:
        db_x("DELETE FROM vp_claims WHERE user_id=?", (uid,))     # activation failed -> let them retry
        raise
    bot.send_message(chat_id, head("🎉", f"{plan['name']} সক্রিয় হয়েছে!", "ভেরিফিকেশন সফল")
                     + f"<blockquote>⏳ মেয়াদ: {fmt_ts(exp)}</blockquote>\n\n🎛 কন্ট্রোল প্যানেল থেকে প্রজেক্ট চালু করুন।",
                     reply_markup=main_menu(uid))


@bot.message_handler(commands=["vpreset"])
def cmd_vpreset(m):
    """Admin: /vpreset <user_id>  -> lets that user take the VPLink free plan once more."""
    if not is_admin(m.from_user.id):
        return
    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return bot.reply_to(m, "ব্যবহার: <code>/vpreset ইউজার_আইডি</code>")
    n = db_x("DELETE FROM vp_claims WHERE user_id=?", (int(parts[1]),)).rowcount
    bot.reply_to(m, "✅ রিসেট হয়েছে।" if n else "ℹ️ এই ইউজারের কোনো ক্লেইম নেই।")


# ==============================================================================
# 12. HANDLERS — admin panel
# ==============================================================================
PLAN_FIELDS = {"name": "নাম", "price": "মূল্য (৳)", "days": "মেয়াদ (দিন)", "max_projects": "সর্বোচ্চ প্রজেক্ট",
               "ram_mb": "RAM (MB)", "cpu_pct": "CPU (%)", "disk_mb": "ডিস্ক (MB)"}
SETTING_LABELS = {"channels": "চ্যানেল (কমা দিয়ে, @username)", "bkash": "bKash নম্বর", "nagad": "Nagad নম্বর",
                  "payment_note": "পেমেন্ট নোট (পেমেন্ট স্ক্রিনে দেখাবে)", "referral_reward": "রেফার রিওয়ার্ড (৳)",
                  "support": "সাপোর্ট @username", "welcome_title": "ওয়েলকাম টাইটেল", "welcome_text": "ওয়েলকাম টেক্সট",
                  "vplink_days": "VPLink ফ্রি প্ল্যানের মেয়াদ (দিন)"}
SETTINGS_PAGE_KEYS = ["channels", "bkash", "nagad", "payment_note", "referral_reward", "support", "vplink_days"]


def adm(fn):
    """admin-only wrapper for callbacks"""
    def w(call, a):
        if not is_admin(call.from_user.id):
            return ack(call, "❌ অনুমতি নেই", True)
        return fn(call, a)
    w.__name__ = fn.__name__
    return w


def admin_home_kb():
    n = db_val("SELECT COUNT(*) FROM payments WHERE status='pending'")
    return kb([("📊 স্ট্যাটস", "a:stats"), (f"💳 পেন্ডিং পেমেন্ট ({n})" if n else "💳 পেন্ডিং পেমেন্ট", "a:pay")],
              [("🧾 পেমেন্ট হিস্টরি", "a:ph"), ("📦 প্ল্যান ম্যানেজ", "a:plans")],
              [("👤 ইউজার ম্যানেজ", "a:users"), ("👑 💎 প্রিমিয়াম ইউজার", "a:premium")],
              [("📦 সব প্রজেক্ট/ফাইল", "a:projects"), ("📢 ব্রডকাস্ট", "a:bc")],
              [("🖥 রানিং প্রজেক্ট", "a:run"), ("⚙️ সেটিংস", "a:set")],
              [("👋 ওয়েলকাম মেসেজ", "a:wl"), ("👮 অ্যাডমিন ম্যানেজ", "a:adm")],
              [("💾 DB ব্যাকআপ", "a:db"), ("📦 ফুল সার্ভার ব্যাকআপ", "a:fullbackup")])


def admin_home_text():
    s = db_stats()
    return (head("🛠", "অ্যাডমিন প্যানেল", "পূর্ণ নিয়ন্ত্রণ আপনার হাতে")
            + f"<blockquote>👥 ইউজার: <b>{s['users']}</b>  •  🆕 আজ: <b>{s['today']}</b>\n"
              f"📦 প্রজেক্ট: <b>{s['projects']}</b> (🟢 {s['running']})\n"
              f"💳 পেন্ডিং: <b>{s['pending']}</b>  •  💰 আয়: <b>৳{s['revenue']:g}</b></blockquote>")


@bot.message_handler(commands=["admin"])
@_quick_cmd(M_ADMIN, admin_only=True)
def cmd_admin(m):
    if is_admin(m.from_user.id):
        bot.send_message(m.chat.id, admin_home_text(), reply_markup=admin_home_kb())


@menu(M_ADMIN)
def m_admin(m):
    if is_admin(m.from_user.id):
        bot.send_message(m.chat.id, admin_home_text(), reply_markup=admin_home_kb())



# ------------------------------------------------------------------ full server migration backup
# Creates one complete ZIP snapshot, then sends it as Telegram-safe numbered parts.
# The archive contains the bot source/config, .env, requirements, SQLite database snapshot,
# and the complete data/ directory (projects, project files, user/project records, logs, backups).
FULL_BACKUP_PART_SIZE = 45 * 1024 * 1024  # leave headroom below Telegram's usual 50 MB bot upload limit


def _full_migration_backup_archive(work_dir):
    work_dir = Path(work_dir)
    archive = work_dir / f"pyhost_full_migration_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    db_snapshot = work_dir / "host_snapshot.db"
    db_backup_to(db_snapshot)

    excluded_dirs = {"__pycache__", ".git", "node_modules", ".venv", "venv"}
    manifest = {
        "created_at": datetime.now(tz=_TZ).isoformat() if _TZ else datetime.now().isoformat(),
        "backup_type": "PyHost Cloud full migration backup",
        "restore_note": "Extract this ZIP into the bot application directory on the new host. Read RESTORE_README.txt.",
        "files": []
    }

    def add_file(zf, file_path, archive_name):
        file_path = Path(file_path)
        digest = hashlib.sha256()
        with open(file_path, "rb") as src:
            while True:
                block = src.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        zf.write(file_path, archive_name)
        manifest["files"].append({
            "path": archive_name,
            "size": file_path.stat().st_size,
            "sha256": digest.hexdigest()
        })

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        # Include all ordinary files beside the bot (source, .env/env, requirements, config files).
        for item in sorted(BASE_DIR.iterdir(), key=lambda x: x.name.lower()):
            if item.is_file() and item.name not in {"host_snapshot.db"}:
                if item.resolve() == archive.resolve() or item.name.endswith((".pyc", ".pyo")):
                    continue
                add_file(zf, item, item.name)

        # Replace the live SQLite file with a consistent SQLite backup snapshot.
        add_file(zf, db_snapshot, "data/host.db")

        # Preserve all app-local folders, including data/projects, logs, backups and any
        # additional persistent folders created beside the bot. Do not copy virtualenvs/caches.
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = sorted(
                d for d in dirs
                if d not in excluded_dirs and not (Path(root) / d).is_symlink()
            )
            for filename in sorted(files):
                full = Path(root) / filename
                if full.is_symlink():
                    continue
                if full.resolve() == DB_PATH.resolve() or filename in {
                    DB_PATH.name + "-wal", DB_PATH.name + "-shm"
                }:
                    continue
                if full.resolve() == db_snapshot.resolve() or full.resolve() == archive.resolve():
                    continue
                rel = full.relative_to(BASE_DIR).as_posix()
                add_file(zf, full, rel)

        readme = """PYHOST CLOUD — FULL SERVER MIGRATION BACKUP

CONTENTS
- Bot Python source and files beside it (including .env/env and requirements.txt when present)
- A consistent SQLite database snapshot at data/host.db
- The complete persistent data/ directory: projects, user/project data, logs and backup files
- manifest.json with SHA-256 checksums for the archived files

RESTORE ON A NEW UBUNTU/DEBIAN HOST
1. Install Python, venv, pip and any OS packages required by your projects.
2. Create the bot application directory (for example /opt/pyhost).
3. Extract ALL parts of this backup into one ZIP, then extract that ZIP into the application directory.
4. Keep the original .env private. Check BOT_TOKEN and ADMIN_IDS; update other host-specific values if needed.
5. Create the Python environment and install the bot requirements:
   python3 -m venv /opt/venv
   /opt/venv/bin/pip install -r requirements.txt
6. If using ISOLATE_USERS=1, recreate the required Linux user/permission setup on the new host.
7. Start the bot with the same entry-point filename included in this backup.
8. Check the logs and test project start/stop, file access and bot functions.

IMPORTANT
- Stop the old bot before switching the same BOT_TOKEN to the new host; Telegram bots should not poll from both hosts.
- Host-specific OS users, systemd services, firewall rules, external databases/services and installed OS packages are not recreated automatically.
- Keep every numbered ZIP part. The archive is incomplete if any part is missing.
- The archive contains .env secrets and user/project data. Store it securely and do not share it publicly.
"""
        zf.writestr("RESTORE_README.txt", readme)
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return archive


def _send_file_in_parts(chat_id, archive_path):
    archive_path = Path(archive_path)
    total_size = archive_path.stat().st_size
    part_count = (total_size + FULL_BACKUP_PART_SIZE - 1) // FULL_BACKUP_PART_SIZE
    archive_hash = hashlib.sha256()
    with open(archive_path, "rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            archive_hash.update(block)

    bot.send_message(
        chat_id,
        "📦 <b>ফুল মাইগ্রেশন ব্যাকআপ তৈরি হয়েছে</b>\n"
        f"মোট সাইজ: <b>{human_size(total_size)}</b>\n"
        f"মোট পার্ট: <b>{part_count}</b>\n"
        f"SHA-256: <code>{archive_hash.hexdigest()}</code>\n\n"
        "সবগুলো পার্ট ডাউনলোড করে একই ফোল্ডারে রাখুন। এরপর নিচের নির্দেশনা অনুযায়ী "
        "একত্র করে ZIP extract করুন।"
    )

    with open(archive_path, "rb") as src:
        for index in range(1, part_count + 1):
            chunk = src.read(FULL_BACKUP_PART_SIZE)
            if not chunk:
                break
            part_name = f"{archive_path.stem}.part{index:03d}-of-{part_count:03d}"
            caption = (
                f"📦 Full Migration Backup — Part {index}/{part_count}\n"
                f"Archive: {archive_path.name}\n"
                "সব পার্ট সংগ্রহ করুন; কোনো পার্ট বাদ দেবেন না।"
            )
            bot.send_document(
                chat_id,
                (part_name, chunk),
                caption=caption,
                timeout=300
            )
            time.sleep(0.8)

    bot.send_message(
        chat_id,
        "✅ <b>সব ব্যাকআপ পার্ট পাঠানো হয়েছে।</b>\n\n"
        "কম্পিউটার/Linux-এ পার্টগুলো একত্র করতে একই নামের ক্রমানুসারী পার্টগুলো concatenate করুন:\n"
        "<code>cat pyhost_full_migration_*.part* &gt; full_migration.zip</code>\n"
        "তারপর ZIP integrity পরীক্ষা করে extract করুন:\n"
        "<code>unzip -t full_migration.zip</code>\n"
        "<code>unzip full_migration.zip -d /opt/pyhost</code>\n\n"
        "মোবাইলে করলে সব পার্ট একই ফোল্ডারে রেখে এমন ZIP join/merge অ্যাপ ব্যবহার করুন "
        "যেটি raw file parts concatenate করতে পারে। ZIP-এর ভেতরের RESTORE_README.txt-ও পড়ুন।"
    )


@cb("afull")
def c_admin_full_backup(call, a):
    if not is_owner(call.from_user.id):
        return ack(call, "❌ শুধু মেইন অ্যাডমিন ফুল সার্ভার ব্যাকআপ নিতে পারবেন", True)
    ack(call, "📦 সম্পূর্ণ মাইগ্রেশন ব্যাকআপ তৈরি হচ্ছে…")

    def job():
        try:
            with tempfile.TemporaryDirectory(prefix="pyhost_full_backup_") as td:
                with work(call.message.chat.id, "fullbackup"):
                    archive = _full_migration_backup_archive(td)
                    _send_file_in_parts(call.message.chat.id, archive)
        except Exception as exc:
            log.exception("full migration backup failed")
            try:
                bot.send_message(call.message.chat.id, f"❌ ফুল ব্যাকআপ ব্যর্থ: {esc(type(exc).__name__)}: {esc(exc)}")
            except Exception:
                pass

    threading.Thread(target=job, daemon=True, name="fullmigrationbackup").start()


@cb("a")
@adm
def c_admin(call, a):
    sub = a[0] if a else "home"
    back = kb([("◀️ অ্যাডমিন", "a:home")])
    if sub == "home":
        clear_state(call.from_user.id)
        show(call, admin_home_text(), admin_home_kb())
    elif sub == "stats":
        s = db_stats()
        vm, disk = psutil.virtual_memory(), psutil.disk_usage(str(DATA_DIR))
        cpu = psutil.cpu_percent(interval=0.3)
        _hr = hosting_ram()
        hr_line = (f"📦 হোস্টিং RAM: {human_size(_hr[0])} / {human_size(_hr[1])}  {bar(_hr[0], _hr[1])}" if _hr
                   else "📦 হোস্টিং RAM: আলাদা লিমিট পাওয়া যায়নি (উপরের RAM-ই পুরো সার্ভারের)")
        show(call, (head("📊", "স্ট্যাটস", "রিয়েল-টাইম ওভারভিউ")
                    + f"<blockquote>👥 ইউজার: <b>{s['users']}</b> (ভেরিফাইড {s['verified']}, ব্যান {s['banned']})\n"
                      f"🆕 গত ২৪ ঘণ্টা: <b>{s['today']}</b>\n💎 পেইড ইউজার: <b>{s['paid']}</b></blockquote>\n"
                      f"<blockquote>📦 প্রজেক্ট: <b>{s['projects']}</b> (🟢 {s['running']} চলছে)\n"
                      f"💳 পেন্ডিং পেমেন্ট: <b>{s['pending']}</b>\n💰 মোট আয়: <b>৳{s['revenue']:g}</b></blockquote>\n"
                      f"🖥 <b>সার্ভার</b>\n"
                      f"<blockquote>🧠 CPU: {cpu}%  {bar(cpu, 100)}\n"
                      f"💾 RAM: {human_size(vm.used)} / {human_size(vm.total)}  {bar(vm.used, vm.total)}\n"
                      f"{hr_line}\n"
                      f"💽 ডিস্ক: {human_size(disk.used)} / {human_size(disk.total)}  {bar(disk.used, disk.total)}</blockquote>"),
             kb([("🔄 রিফ্রেশ", "a:stats")], [("◀️ অ্যাডমিন", "a:home")]))
    elif sub == "pay":
        pend = db_pending_payments()
        if not pend:
            return show(call, "✅ কোনো পেন্ডিং পেমেন্ট নেই।", back)
        show(call, f"💳 পেন্ডিং: {len(pend)}টি (নিচে পাঠালাম)", back)
        for pay in pend[:15]:
            notify(call.from_user.id, payment_text(pay, db_get_user(pay["user_id"])),
                   kb([("✅ Approve", f"payok:{pay['id']}"), ("❌ Reject", f"payno:{pay['id']}")]))
    elif sub == "plans":
        rows = [[(f"{'🟢' if p['active'] else '⚪'} {p['name']} — ৳{p['price']:g}", f"ap:{p['id']}")] for p in db_list_plans()]
        rows += [[("➕ নতুন প্ল্যান", "apn")], [("◀️ অ্যাডমিন", "a:home")]]
        show(call, "📦 <b>প্ল্যান ম্যানেজ</b>\n(FREE প্ল্যানের 'মেয়াদ' = ফ্রি ট্রায়াল দিন)", kb(*rows))
    elif sub == "premium":
        rows = []
        premium = db_q("SELECT * FROM users WHERE plan_id!='free' AND plan_expires>? ORDER BY plan_expires DESC LIMIT 50",
                       (time.time(),))
        for u in premium:
            uname = ("@" + u["username"]) if u["username"] else "ID " + str(u["id"])
            rows.append([(f"👑 {uname} · {esc(u['first_name'] or 'User')[:18]}", f"auvcard:{u['id']}")])
        rows.append([("🟢 ➕ ইউজার খুঁজুন", "a:users")])
        rows.append([("◀️ অ্যাডমিন", "a:home")])
        show(call, f"👑 <b>প্রিমিয়াম ইউজার</b> — {len(premium)} জন (সর্বোচ্চ ৫০ জন)", kb(*rows))
    elif sub == "projects":
        rows = []
        for p in db_q("SELECT * FROM projects ORDER BY id DESC LIMIT 50"):
            u = db_get_user(p["user_id"])
            uname = ("@" + u["username"]) if u and u["username"] else str(p["user_id"])
            status = "🟢" if is_running(p["id"]) else "🔴"
            rows.append([(f"📦 {status} {p['name'][:16]} · {uname[:15]}", f"fm:{p['id']}")])
        rows.append([("◀️ অ্যাডমিন", "a:home")])
        show(call, "📦 <b>সব ইউজারের প্রজেক্ট</b>\nপ্রজেক্টে চাপলে তার ফাইল ম্যানেজার খুলবে।", kb(*rows))
    elif sub == "users":
        set_state(call.from_user.id, "a_user")
        show(call, "👤 ইউজারের Telegram ID পাঠান:\n\nবাতিল: /cancel", back)
    elif sub == "bc":
        set_state(call.from_user.id, "a_bc")
        show(call, "📢 <b>ব্রডকাস্ট</b>\nযে মেসেজ (টেক্সট/ছবি/ভিডিও/ফাইল) সবাইকে পাঠাতে চান সেটা এখন পাঠান:\n\nবাতিল: /cancel", back)
    elif sub == "run":
        rows = []
        for pid, proc in list(RUNNING.items())[:25]:
            p = db_get_project(pid)
            if p:
                s = proc_stats(p)
                rows.append([(f"⏹ {p['name'][:14]} | {p['user_id']} | {s['ram']:.0f}MB", f"ak:{pid}")])
        rows.append([("◀️ অ্যাডমিন", "a:home")])
        show(call, f"🖥 <b>রানিং প্রজেক্ট ({len(RUNNING)})</b>\nবাটনে চাপলে বন্ধ হবে।", kb(*rows))
    elif sub == "set":
        rows = [[(f"{SETTING_LABELS[k]}: {(db_get_setting(k) or '—').replace(chr(10), ' ')[:20]}", f"as:{k}")]
                for k in SETTINGS_PAGE_KEYS]
        rows.append([(f"💗 bKash: {'ON ✅' if db_get_setting('bkash_on') != '0' else 'OFF ⛔'}", "ast:bkash_on"),
                     (f"🟠 Nagad: {'ON ✅' if db_get_setting('nagad_on') != '0' else 'OFF ⛔'}", "ast:nagad_on")])
        _vpp = db_get_plan(db_get_setting("vplink_plan") or "")
        rows.append([(f"🎁 VPLink: {'ON ✅' if db_get_setting('vplink_on') == '1' else 'OFF ⛔'}", "ast:vplink_on"),
                     (f"📦 VPLink প্ল্যান: {_vpp['name'] if _vpp else '—'}", "vps")])
        rows.append([("◀️ অ্যাডমিন", "a:home")])
        show(call, head("⚙️", "সেটিংস", "বট ও পেমেন্ট কনফিগারেশন")
                   + "পরিবর্তন করতে বাটনে চাপুন।\n"
                     "<blockquote>💗/🟠 বাটনে চাপলে সেই পেমেন্ট মাধ্যম চালু/বন্ধ হবে।\n"
                     "📢 চ্যানেল ভেরিফিকেশন: বটকে চ্যানেলে Admin বানাতে হবে।\n"
                     + ("🎁 VPLink: চালু করতে <code>.env</code>-এ VPLINK_API_KEY দিন, প্ল্যান বেছে ON করুন।"
                        if not VPLINK_API_KEY else "🎁 VPLink: ইউজার ভেরিফাই করলে সিলেক্ট করা প্ল্যান পাবে।")
                     + "</blockquote>",
             kb(*rows))
    elif sub == "ph":
        icon = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
        lines = [f"{icon.get(r['status'], '•')} #{r['id']} • <code>{r['user_id']}</code> • {esc(r['plan_id'])} • "
                 f"৳{r['amount']:g} • {METHODS.get(r['method'], r['method'])} • <code>{esc(r['trx_id'])}</code>"
                 for r in db_q("SELECT * FROM payments ORDER BY id DESC LIMIT 15")]
        show(call, "🧾 <b>পেমেন্ট হিস্টরি (শেষ ১৫টি)</b>\n\n" + ("\n".join(lines) or "কোনো পেমেন্ট নেই।"),
             kb([("💳 পেন্ডিং দেখুন", "a:pay")], [("◀️ অ্যাডমিন", "a:home")]))
    elif sub == "wl":
        vid = (db_get_setting("welcome_video") or "").strip()
        vrow = [("🎬 ভিডিও সেট করুন", "awv")] + ([("🗑 ভিডিও সরান", "awvx")] if vid else [])
        show(call, "👋 <b>ওয়েলকাম মেসেজ</b> — ইউজার নতুন এসে এটাই দেখবে:\n━━━━━━━━━━━━━━\n" + welcome_message()
             + "\n\n🎬 ভিডিও: " + ("✅ সেট করা আছে" if vid else "❌ সেট করা নেই"),
             kb([("✏️ টাইটেল বদলান", "as:welcome_title"), ("✏️ টেক্সট বদলান", "as:welcome_text")],
                vrow, [("👁 প্রিভিউ", "awp"), ("♻️ ডিফল্টে ফেরান", "awr")], [("◀️ অ্যাডমিন", "a:home")]))
    elif sub == "adm":
        if not is_owner(call.from_user.id):
            return ack(call, "❌ শুধু মেইন অ্যাডমিন (.env-এর ADMIN_IDS) অ্যাডমিন ম্যানেজ করতে পারবে", True)
        extra = sorted(extra_admins())
        rows = [[(f"🗑 সরান: {i}", f"ard:{i}")] for i in extra]
        rows += [[("➕ অ্যাডমিন যোগ", "aad")], [("◀️ অ্যাডমিন", "a:home")]]
        show(call, ("👮 <b>অ্যাডমিন ম্যানেজ</b>\n\n👑 মেইন (.env): " + ", ".join(f"<code>{i}</code>" for i in sorted(ADMIN_IDS))
                    + "\n🛡 যোগ করা অ্যাডমিন: " + (", ".join(f"<code>{i}</code>" for i in extra) or "কেউ নেই")
                    + "\n\nযোগ করা অ্যাডমিন পেমেন্ট অ্যাপ্রুভ/ইউজার/প্ল্যান/সেটিংস ম্যানেজ করতে পারবে। "
                      "DB ব্যাকআপ ও অ্যাডমিন ম্যানেজ শুধু মেইন অ্যাডমিন করতে পারে।"), kb(*rows))
    elif sub == "fullbackup":
        if not is_owner(call.from_user.id):
            return ack(call, "❌ শুধু মেইন অ্যাডমিন ফুল সার্ভার ব্যাকআপ নিতে পারবেন", True)
        # Delegate to the same protected callback used by the backup button.
        return c_admin_full_backup(call, [])
    elif sub == "db":
        if not is_owner(call.from_user.id):
            return ack(call, "❌ শুধু মেইন অ্যাডমিন (.env-এর ADMIN_IDS) ডাটাবেস ব্যাকআপ নিতে পারবে", True)
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/host_{time.strftime('%Y%m%d_%H%M')}.db"
            db_backup_to(path)
            with open(path, "rb") as f:
                bot.send_document(call.message.chat.id, f, caption="💾 ডাটাবেস ব্যাকআপ")


@cb("ak")
@adm
def c_kill(call, a):
    pid = int(a[0])
    p = db_get_project(pid)
    stop_project(pid)
    if p:
        notify(p["user_id"], f"⛔ অ্যাডমিন আপনার প্রজেক্ট <b>{esc(p['name'])}</b> বন্ধ করেছে।")
    ack(call, "⏹ বন্ধ করা হয়েছে")
    c_admin(call, ["run"])


# ------------------------------------------------------------------ plans
@cb("ap")
@adm
def c_plan(call, a):
    p = db_get_plan(a[0])
    if not p:
        return ack(call, "প্ল্যান নেই", True)
    rows = [[(f"✏️ {lbl}", f"apf:{p['id']}:{f}")] for f, lbl in PLAN_FIELDS.items()]
    rows.append([(f"{'🟢 চালু' if p['active'] else '⚪ বন্ধ'} (টগল)", f"apt:{p['id']}:active"),
                 (f"♻️ অটো-রিস্টার্ট {'ON' if p['auto_restart'] else 'OFF'}", f"apt:{p['id']}:auto_restart")])
    if p["id"] != "free":
        rows.append([("🗑 ডিলিট", f"apd:{p['id']}")])
    rows.append([("◀️ পেছনে", "a:plans")])
    show(call, f"📦 <b>প্ল্যান: {esc(p['id'])}</b>\n\n{plan_card(p)}", kb(*rows))


@cb("apf")
@adm
def c_plan_field(call, a):
    set_state(call.from_user.id, "a_planf", plan=a[0], field=a[1])
    show(call, f"✏️ নতুন <b>{PLAN_FIELDS[a[1]]}</b> লিখুন:\n\nবাতিল: /cancel")


@state("a_planf")
def s_planf(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text":
        return
    f, v = st["field"], m.text.strip()
    try:
        value = v if f == "name" else (float(v) if f == "price" else int(v))
        if f != "name" and value < 0:
            raise ValueError
    except ValueError:
        return bot.send_message(m.chat.id, "❌ সঠিক সংখ্যা দিন।")
    db_update_plan(st["plan"], **{f: value})
    clear_state(m.from_user.id)
    bot.send_message(m.chat.id, "✅ আপডেট হয়েছে।", reply_markup=kb([("📦 প্ল্যান", f"ap:{st['plan']}")]))


@cb("apt")
@adm
def c_plan_toggle(call, a):
    p = db_get_plan(a[0])
    if p and a[1] in ("active", "auto_restart"):
        db_update_plan(p["id"], **{a[1]: 0 if p[a[1]] else 1})
    c_plan(call, [a[0]])


@cb("apd")
@adm
def c_plan_del(call, a):
    show(call, f"⚠️ প্ল্যান <b>{esc(a[0])}</b> ডিলিট করবেন? (এই প্ল্যানের ইউজাররা FREE-তে নেমে যাবে)",
         kb([("✅ হ্যাঁ", f"apdy:{a[0]}"), ("❌ না", f"ap:{a[0]}")]))


@cb("apdy")
@adm
def c_plan_del_yes(call, a):
    db_x("UPDATE users SET plan_id='free', plan_expires=NULL WHERE plan_id=?", (a[0],))
    db_delete_plan(a[0])
    c_admin(call, ["plans"])


@cb("apn")
@adm
def c_plan_new(call, a):
    set_state(call.from_user.id, "a_plann")
    show(call, "➕ <b>নতুন প্ল্যান</b>\nএই ফরম্যাটে পাঠান:\n<code>id|নাম|দাম|দিন|প্রজেক্ট|ram_mb|cpu%|disk_mb</code>\n"
               "যেমন: <code>vip|💎 VIP|499|30|100|4096|300|10000</code>\n\nবাতিল: /cancel")


@state("a_plann")
def s_plann(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text":
        return
    parts = [x.strip() for x in m.text.split("|")]
    try:
        assert len(parts) == 8 and re.fullmatch(r"[a-z0-9_]{2,15}", parts[0]) and not db_get_plan(parts[0])
        db_add_plan(parts[0], parts[1], float(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), int(parts[6]),
                    int(parts[7]))
    except Exception:  # noqa
        return bot.send_message(m.chat.id, "❌ ফরম্যাট ভুল বা id আগে থেকেই আছে (id: a-z 0-9 _)।")
    clear_state(m.from_user.id)
    bot.send_message(m.chat.id, "✅ প্ল্যান যোগ হয়েছে।", reply_markup=kb([("📦 প্ল্যান", "a:plans")]))


# ------------------------------------------------------------------ users
def user_card(u):
    st = db_plan_state(u)
    uname = f"@{u['username']}" if u["username"] else "—"
    return (head("👤", esc(u["first_name"] or "—"), uname)
            + f"<blockquote>🆔 <code>{u['id']}</code>\n📅 {fmt_ts(u['joined_at'])}</blockquote>\n"
              f"<blockquote>👑 {st['plan']['name']} ({plan_left(st)})\n"
              f"💰 ৳{u['balance']:g} • 👥 রেফার {u['ref_count']}\n"
              f"📦 প্রজেক্ট: {len(db_user_projects(u['id']))} • 🚫 ব্যান: {'হ্যাঁ' if u['banned'] else 'না'}</blockquote>")


def user_kb(u):
    i = u["id"]
    contact = ([(f"💬 চ্যাট করুন @{u['username']}", f"https://t.me/{u['username']}")]
               if u.get("username") else [(f"🆔 ID: {i} (username নেই)", "a:users")])
    return kb(contact,
              [("🚫 আনব্যান" if u["banned"] else "🚫 ব্যান", f"aub:{i}"), ("💰 ব্যালেন্স যোগ/কমান", f"auv:{i}")],
              [("🎁 প্ল্যান দিন", f"aug:{i}"), ("♻️ ট্রায়াল রিসেট", f"aur:{i}")],
              [("❌ প্ল্যান বাতিল", f"aux:{i}"), ("✉️ মেসেজ", f"aum:{i}")],
              [("◀️ অ্যাডমিন", "a:home")])


@state("a_user")
def s_user(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text" or not m.text.strip().isdigit():
        return bot.send_message(m.chat.id, "❌ সংখ্যায় ID দিন।")
    u = db_get_user(int(m.text.strip()))
    if not u:
        return bot.send_message(m.chat.id, "❌ ইউজার পাওয়া যায়নি।")
    clear_state(m.from_user.id)
    bot.send_message(m.chat.id, user_card(u), reply_markup=user_kb(u))


def _refresh_user(call, uid):
    u = db_get_user(int(uid))
    show(call, user_card(u), user_kb(u))


@cb("auvcard")
@adm
def c_user_card(call, a):
    u = db_get_user(int(a[0]))
    if not u:
        return ack(call, "ইউজার পাওয়া যায়নি", True)
    show(call, user_card(u), user_kb(u))


@cb("aub")
@adm
def c_ban(call, a):
    u = db_get_user(int(a[0]))
    db_update_user(u["id"], banned=0 if u["banned"] else 1)
    if not u["banned"]:
        for p in db_user_projects(u["id"]):
            stop_project(p["id"])
    _refresh_user(call, a[0])


@cb("auv")
@adm
def c_bal(call, a):
    set_state(call.from_user.id, "a_bal", uid=int(a[0]))
    show(call, "💰 পরিমাণ লিখুন (যোগ: <code>50</code>, কমানো: <code>-20</code>):\n\nবাতিল: /cancel")


@state("a_bal")
def s_bal(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text":
        return
    try:
        amt = float(m.text.strip())
    except ValueError:
        return bot.send_message(m.chat.id, "❌ সংখ্যা দিন।")
    db_add_balance(st["uid"], amt)
    clear_state(m.from_user.id)
    notify(st["uid"], f"💰 আপনার ব্যালেন্সে ৳{amt:+g} পরিবর্তন করা হয়েছে।")
    u = db_get_user(st["uid"])
    bot.send_message(m.chat.id, "✅ হয়েছে।\n\n" + user_card(u), reply_markup=user_kb(u))


@cb("aug")
@adm
def c_give(call, a):
    rows = [[(f"{p['name']} ({p['days']}দিন)", f"augp:{a[0]}:{p['id']}")] for p in db_list_plans(paid_only=True)]
    rows.append([("◀️ পেছনে", f"aus:{a[0]}")])
    show(call, "🎁 কোন প্ল্যান দেবেন?", kb(*rows))


@cb("augp")
@adm
def c_give_plan(call, a):
    exp = db_activate_plan(int(a[0]), a[1])
    notify(int(a[0]), f"🎁 অ্যাডমিন আপনাকে <b>{db_get_plan(a[1])['name']}</b> প্ল্যান দিয়েছে!\n⏳ মেয়াদ: {fmt_ts(exp)}")
    _refresh_user(call, a[0])


@cb("aus")
@adm
def c_show_user(call, a):
    _refresh_user(call, a[0])


@cb("aur")
@adm
def c_reset_trial(call, a):
    db_update_user(int(a[0]), trial_started=None, trial_notified=0)
    ack(call, "ট্রায়াল রিসেট হয়েছে")
    _refresh_user(call, a[0])


@cb("aux")
@adm
def c_cancel_plan(call, a):
    db_update_user(int(a[0]), plan_id="free", plan_expires=None)
    ack(call, "প্ল্যান বাতিল")
    _refresh_user(call, a[0])


@cb("aum")
@adm
def c_msg(call, a):
    set_state(call.from_user.id, "a_msg", uid=int(a[0]))
    show(call, "✉️ মেসেজ লিখুন (ইউজারকে পাঠানো হবে):\n\nবাতিল: /cancel")


@state("a_msg")
def s_msg(m, st):
    if is_admin(m.from_user.id) and m.content_type == "text":
        ok = notify(st["uid"], f"📩 <b>অ্যাডমিন:</b>\n{esc(m.text)}")
        clear_state(m.from_user.id)
        bot.send_message(m.chat.id, "✅ পাঠানো হয়েছে।" if ok else "❌ পাঠানো যায়নি (ইউজার বট ব্লক করেছে?)")


# ------------------------------------------------------------------ broadcast
@state("a_bc")
def s_bc(m, st):
    if not is_admin(m.from_user.id):
        return
    set_state(m.from_user.id, "a_bc_go", chat=m.chat.id, mid=m.message_id)
    bot.send_message(m.chat.id, f"📢 এই মেসেজটি <b>{len(db_all_user_ids())}</b> জনকে পাঠাবেন?",
                     reply_markup=kb([("✅ পাঠান", "abc:go"), ("❌ বাতিল", "abc:no")]))


@cb("abc")
@adm
def c_bc(call, a):
    uid = call.from_user.id
    st = get_state(uid)
    if a[0] == "no" or not st or st["action"] != "a_bc_go":
        clear_state(uid)
        return show(call, "❌ বাতিল।")
    clear_state(uid)
    show(call, "⏳ ব্রডকাস্ট শুরু হয়েছে…")

    def job():
        sent = failed = 0
        for target in db_all_user_ids():
            try:
                bot.copy_message(target, st["chat"], st["mid"])
                sent += 1
            except Exception:  # noqa
                failed += 1
            time.sleep(0.05)
        notify(uid, f"📢 ব্রডকাস্ট শেষ\n✅ সফল: {sent}\n❌ ব্যর্থ: {failed}")
    threading.Thread(target=job, daemon=True).start()


# ------------------------------------------------------------------ settings
@cb("as")
@adm
def c_setting(call, a):
    if a[0] in SETTING_LABELS:
        set_state(call.from_user.id, "a_set", key=a[0])
        hint = ("\n<blockquote>📢 @username বা t.me লিংক দিন (একাধিক হলে কমা দিয়ে)। "
                "ভেরিফিকেশনের জন্য বটকে চ্যানেলে <b>Admin</b> বানাতে হবে।</blockquote>\n") if a[0] == "channels" else ""
        show(call, head("⚙️", SETTING_LABELS[a[0]], "সেটিং পরিবর্তন")
                   + f"বর্তমান: <code>{esc(db_get_setting(a[0]) or '—')}</code>\n{hint}\n"
                     f"নতুন মান পাঠান (খালি/ডিফল্ট করতে <code>-</code>):\n\nবাতিল: /cancel")


@state("a_set")
def s_set(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text":
        return
    v = m.text.strip()
    v = "" if v == "-" else v
    key = st["key"]
    if key == "referral_reward":
        try:
            float(v)
        except ValueError:
            return bot.send_message(m.chat.id, "❌ সংখ্যা দিন।")
    if key == "vplink_days" and not (v.isdigit() and 1 <= int(v) <= 365):
        return bot.send_message(m.chat.id, "❌ ১ থেকে ৩৬৫ এর মধ্যে সংখ্যা দিন।")
    if key == "channels":      # accept "@name", "name" or a t.me link
        v = ",".join(c for c in (_norm_channel(x) for x in v.split(",")) if c)
    limits = {"welcome_title": 60, "welcome_text": 2000, "payment_note": 300}
    if key in limits and len(v) > limits[key]:
        return bot.send_message(m.chat.id, f"❌ সর্বোচ্চ {limits[key]} অক্ষর (আপনারটা {len(v)})। ছোট করে আবার পাঠান।")
    db_set_setting(key, v)
    clear_state(m.from_user.id)
    back, lbl = ("a:wl", "👋 ওয়েলকাম মেসেজ") if key.startswith("welcome") else ("a:set", "⚙️ সেটিংস")
    bot.send_message(m.chat.id, "✅ সেভ হয়েছে।", reply_markup=kb([(lbl, back)]))


@cb("ast")
@adm
def c_setting_toggle(call, a):
    if a and a[0] == "vplink_on" and db_get_setting("vplink_on") != "1":
        if not VPLINK_API_KEY:
            return ack(call, "❌ আগে .env-এ VPLINK_API_KEY দিন।", True)
        if not vp_plan():
            return ack(call, "❌ আগে 📦 VPLink প্ল্যান বেছে নিন।", True)
    if a and a[0] in ("bkash_on", "nagad_on", "vplink_on"):
        db_set_setting(a[0], "1" if db_get_setting(a[0]) == "0" else "0")
    c_admin(call, ["set"])


@cb("awr")
@adm
def c_welcome_reset(call, a):
    for k in ("welcome_title", "welcome_text"):
        db_set_setting(k, DEFAULT_SETTINGS[k])
    ack(call, "♻️ ডিফল্ট ওয়েলকাম সেট হয়েছে")
    c_admin(call, ["wl"])


@cb("awv")
@adm
def c_welcome_video(call, a):
    set_state(call.from_user.id, "a_wvid")
    show(call, "🎬 ওয়েলকাম ভিডিও পাঠান (ভিডিও বা GIF), অথবা ভিডিওর লিংক দিন।\n"
               "<blockquote>ভিডিও সরাসরি ভিডিও হিসেবে পাঠান (ফাইল হিসেবে নয়)। এটি ওয়েলকাম মেসেজের সাথে দেখানো হবে।</blockquote>\n\n"
               "বাতিল: /cancel")


@state("a_wvid")
def s_wvid(m, st):
    if not is_admin(m.from_user.id):
        return
    val = None
    if m.content_type == "video":
        val = f"video:{m.video.file_id}"
    elif m.content_type == "animation":
        val = f"animation:{m.animation.file_id}"
    elif m.content_type == "text" and m.text.strip().lower().startswith(("http://", "https://")):
        val = f"video:{m.text.strip()}"
    if not val:
        return bot.send_message(m.chat.id, "❌ ভিডিও, GIF অথবা ভিডিওর লিংক পাঠান।")
    db_set_setting("welcome_video", val)
    clear_state(m.from_user.id)
    bot.send_message(m.chat.id, "✅ ওয়েলকাম ভিডিও সেট হয়েছে।",
                     reply_markup=kb([("👁 প্রিভিউ", "awp")], [("👋 ওয়েলকাম মেসেজ", "a:wl")]))


@cb("awvx")
@adm
def c_welcome_video_off(call, a):
    db_set_setting("welcome_video", "")
    ack(call, "🗑 ভিডিও সরানো হয়েছে")
    c_admin(call, ["wl"])


@cb("awp")
@adm
def c_welcome_preview(call, a):
    ack(call)
    ok = send_welcome(call.message.chat.id, welcome_message(call.from_user.first_name), main_menu(call.from_user.id))
    if not ok and (db_get_setting("welcome_video") or "").strip():
        bot.send_message(call.message.chat.id, "⚠️ ভিডিও পাঠানো যায়নি — নতুন করে সেট করুন।")


@cb("vps")
@adm
def c_vp_plan_pick(call, a):
    """Admin: choose which plan VPLink verification gives."""
    if a:
        p = db_get_plan(a[0])
        if p and p["id"] != "free":
            db_set_setting("vplink_plan", p["id"])
        return c_admin(call, ["set"])
    plans = db_list_plans(paid_only=True)
    rows = [[(("✅ " if p["id"] == db_get_setting("vplink_plan") else "") + f"{p['name']} — ৳{p['price']:g}", f"vps:{p['id']}")]
            for p in plans]
    rows.append([("◀️ সেটিংস", "a:set")])
    show(call, head("🎁", "VPLink প্ল্যান বেছে নিন", "ভেরিফাই করলে ইউজার এই প্ল্যান পাবে") + "প্ল্যান সিলেক্ট করুন 👇", kb(*rows))


# ------------------------------------------------------------------ admin management (main admins only)
@cb("aad")
@adm
def c_admin_add(call, a):
    if not is_owner(call.from_user.id):
        return ack(call, "❌ শুধু মেইন অ্যাডমিন", True)
    set_state(call.from_user.id, "a_addadm")
    show(call, "👮 নতুন অ্যাডমিনের Telegram ID পাঠান (সে আগে বটে /start দিয়ে থাকতে হবে):\n\nবাতিল: /cancel")


@state("a_addadm")
def s_addadm(m, st):
    if not is_owner(m.from_user.id) or m.content_type != "text":
        return
    t = m.text.strip()
    if not t.isdigit() or not db_get_user(int(t)):
        return bot.send_message(m.chat.id, "❌ সঠিক ID দিন — ইউজারকে আগে বটে /start দিতে হবে।")
    db_set_setting("admins", ",".join(str(i) for i in sorted(extra_admins() | {int(t)})))
    clear_state(m.from_user.id)
    notify(int(t), "🛡 আপনাকে অ্যাডমিন বানানো হয়েছে। অ্যাডমিন মেনু পেতে /start দিন।")
    bot.send_message(m.chat.id, "✅ অ্যাডমিন যোগ হয়েছে।", reply_markup=kb([("👮 অ্যাডমিন ম্যানেজ", "a:adm")]))


@cb("ard")
@adm
def c_admin_remove(call, a):
    if not is_owner(call.from_user.id):
        return ack(call, "❌ শুধু মেইন অ্যাডমিন", True)
    if a and a[0].isdigit():
        db_set_setting("admins", ",".join(str(i) for i in sorted(extra_admins() - {int(a[0])})))
        ack(call, "🗑 সরানো হয়েছে")
    c_admin(call, ["adm"])


# ==============================================================================
# 13. BACKGROUND JOBS  (expiry reminders, daily DB backup)
# ==============================================================================
RENEW_KB = lambda: kb([("💎 প্ল্যান কিনুন", "up")])


def _expiry_once():
    now = time.time()
    # paid plans ending within 24h
    for u in db_q("SELECT * FROM users WHERE plan_id!='free' AND plan_expires>? AND plan_expires<? AND exp_notified!=plan_expires",
                  (now, now + 86400)):
        plan = db_get_plan(u["plan_id"])
        notify(u["id"], head("⏰", "প্ল্যান শীঘ্রই শেষ হবে!", "রিনিউ করে প্রজেক্ট চালু রাখুন")
               + f"<blockquote>👑 প্ল্যান: <b>{plan['name'] if plan else u['plan_id']}</b>\n"
                 f"⏳ মেয়াদ: {fmt_ts(u['plan_expires'])}</blockquote>\n\nপ্রজেক্ট চালু রাখতে এখনই রিনিউ করুন।", RENEW_KB())
        db_update_user(u["id"], exp_notified=u["plan_expires"])
    # paid plans already expired -> downgrade to free
    for u in db_q("SELECT * FROM users WHERE plan_id!='free' AND plan_expires IS NOT NULL AND plan_expires<=?", (now,)):
        db_update_user(u["id"], plan_id="free", plan_expires=None)
        notify(u["id"], head("⌛", "প্ল্যানের মেয়াদ শেষ", "এখন FREE প্ল্যান প্রযোজ্য")
               + "আপনার পেইড প্ল্যানের মেয়াদ শেষ হয়েছে। চালু রাখতে প্ল্যান রিনিউ করুন।", RENEW_KB())
    # free trial ending within 24h
    days = db_get_plan("free")["days"]
    for u in db_q("SELECT * FROM users WHERE trial_started IS NOT NULL AND trial_notified=0 AND trial_started+?>? AND "
                  "trial_started+?<?", (days * 86400, now, days * 86400, now + 86400)):
        notify(u["id"], head("⏰", "ফ্রি ট্রায়াল শীঘ্রই শেষ!", "২৪ ঘণ্টার মধ্যে মেয়াদ শেষ হবে")
               + "বট চালু রাখতে প্ল্যান কিনুন।", RENEW_KB())
        db_update_user(u["id"], trial_notified=1)


def _expiry_loop():
    while True:
        try:
            _expiry_once()
        except Exception:  # noqa
            log.exception("expiry job")
        time.sleep(600)


def _db_backup_loop():
    time.sleep(120)
    while True:
        try:
            with tempfile.TemporaryDirectory() as td:
                path = f"{td}/host_{time.strftime('%Y%m%d_%H%M')}.db"
                db_backup_to(path)
                for a in ADMIN_IDS:
                    try:
                        with open(path, "rb") as f:
                            bot.send_document(a, f, caption="💾 অটো ডাটাবেস ব্যাকআপ (দৈনিক)")
                    except Exception as e:  # noqa
                        log.info("db backup to %s failed: %s", a, e)
        except Exception:  # noqa
            log.exception("db backup job")
        time.sleep(86400)


# ==============================================================================
# 13b. GITHUB DATA BACKUP — pushes host.db + project files to a private GitHub repo
# ==============================================================================
def _gh_check_repo():
    """Refuses to push if the repo is public (the data contains user information)."""
    global _gh_repo_checked
    if _gh_repo_checked:
        return True, ""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{_gh_slug()}",
                                     headers={"Authorization": f"Bearer {GH_TOKEN}", "User-Agent": "pyhost-backup",
                                              "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            info = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "টোকেন ঠিক নয় বা রিপোতে অ্যাক্সেস নেই (Contents: Read and write পারমিশন লাগবে)"
        if e.code == 404:
            return False, "রিপো পাওয়া যায়নি — DATA_REPO (owner/repo) বা টোকেনের রিপো-অ্যাক্সেস ঠিক করুন"
        return False, f"GitHub API ত্রুটি {e.code}"
    except Exception as e:  # noqa
        return False, f"GitHub-এ যোগাযোগ করা যায়নি: {_gh_redact(e)}"
    if info.get("private") is False:
        return False, "রিপোটি PUBLIC! ইউজারদের ডাটা সুরক্ষিত রাখতে রিপোটি Private করুন"
    _gh_repo_checked = True
    return True, ""


def gh_backup(force=False):
    """Returns (ok, message). Safe to call from any thread."""
    global _gh_last_sig
    if not _gh_enabled():
        return False, "DATA_REPO / DATA_REPO_TOKEN সেট করা নেই"
    if _gh_block_push:
        return False, "রিস্টোর ব্যর্থ হয়েছিল, তাই ভুল ডাটা দিয়ে ব্যাকআপ ওভাররাইট ঠেকাতে পুশ বন্ধ আছে। সমস্যা ঠিক করে বট রিস্টার্ট দিন।"
    if not shutil.which("git"):
        return False, "সার্ভারে git ইন্সটল নেই"
    if not _gh_lock.acquire(blocking=False):
        return False, "আরেকটা ব্যাকআপ চলছে"
    work = Path(tempfile.mkdtemp(prefix="ghbackup_"))
    try:
        ok, why = _gh_check_repo()
        if not ok:
            return False, why
        snap = work / "host.db"
        db_backup_to(snap)
        files = []
        for root, dirs, fns in os.walk(PROJECTS_DIR):
            dirs[:] = [d for d in dirs if d not in GH_SKIP_DIRS]
            for f in fns:
                fp = Path(root) / f
                if f.endswith((".pyc", ".pyo")) or fp.is_symlink():
                    continue
                try:
                    st = fp.stat()
                except OSError:
                    continue
                files.append((fp, st.st_size, int(st.st_mtime)))
        sig = hashlib.sha256(snap.read_bytes())
        for fp, sz, mt in sorted(files, key=lambda x: str(x[0])):
            sig.update(f"{fp}|{sz}|{mt}".encode())
        sig = sig.hexdigest()
        if not force and sig == _gh_last_sig:
            return True, "কোনো পরিবর্তন নেই — ব্যাকআপ বাদ"
        archive = work / "backup.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
            zf.write(snap, "host.db")
            for fp, _sz, _mt in files:
                try:
                    zf.write(fp, "projects/" + fp.relative_to(PROJECTS_DIR).as_posix())
                except OSError:
                    continue
        size_mb = archive.stat().st_size / 1048576
        if size_mb > GH_MAX_MB:
            return False, f"ব্যাকআপ {size_mb:.0f}MB — সীমা {GH_MAX_MB}MB ছাড়িয়েছে (DATA_REPO_MAX_MB বাড়ান বা বড় প্রজেক্ট ফাইল কমান)"
        repo = work / "repo"
        repo.mkdir()
        h, n = hashlib.sha256(), 0
        with open(archive, "rb") as src:
            while True:
                chunk = src.read(GH_PART_MB * 1048576)
                if not chunk:
                    break
                n += 1
                h.update(chunk)
                (repo / f"backup.zip.part{n:03d}").write_bytes(chunk)
        c = sqlite3.connect(str(snap))
        try:
            users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            projs = c.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        finally:
            c.close()
        (repo / "manifest.json").write_text(json.dumps({
            "created_at": datetime.now(tz=_TZ).isoformat() if _TZ else datetime.now().isoformat(),
            "sha256": h.hexdigest(), "parts": n, "size_mb": round(size_mb, 2), "users": users, "projects": projs,
        }, indent=1), encoding="utf-8")
        (repo / "README.txt").write_text("PyHost data backup (auto-generated). Do not edit. Restored automatically on first start.\n")
        for args in (["init", "-q"], ["checkout", "-q", "-b", GH_BRANCH], ["add", "-A"],
                     ["-c", "user.name=pyhost-backup", "-c", "user.email=backup@pyhost.local",
                      "commit", "-q", "-m", f"backup {time.strftime('%Y-%m-%d %H:%M:%S')}"]):
            rc, out = _gh_git(args, repo, 300)
            if rc != 0:
                return False, f"git {args[0]} ব্যর্থ: {out[-200:]}"
        rc, out = _gh_git(["push", "--force", "-q", _gh_url(), f"HEAD:refs/heads/{GH_BRANCH}"], repo, 900)
        if rc != 0:
            return False, f"push ব্যর্থ: {out[-250:]}"
        _gh_last_sig = sig
        return True, f"✅ ব্যাকআপ সম্পন্ন — {users} ইউজার, {projs} প্রজেক্ট, {size_mb:.1f}MB"
    except Exception as e:  # noqa
        log.exception("github backup")
        return False, _gh_redact(f"{type(e).__name__}: {e}")[:250]
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _gh_lock.release()


def _gh_record(ok, msg):
    _gh_status.update(time=time.time(), ok=ok, msg=msg)


def _gh_backup_loop():
    time.sleep(90)
    fails = 0
    while True:
        ok, msg = gh_backup()
        _gh_record(ok, msg)
        if ok:
            fails = 0
            log.info("github backup: %s", msg)
        else:
            fails += 1
            log.warning("github backup failed: %s", msg)
            if fails in (1, 6, 24):
                try:
                    notify_admins(f"⚠️ <b>GitHub ব্যাকআপ ব্যর্থ</b>\n<blockquote>{esc(msg)}</blockquote>")
                except Exception:  # noqa
                    pass
        time.sleep(GH_MINUTES * 60)


def _gh_startup_notice():
    time.sleep(12)
    st = _gh_restore_info["state"]
    try:
        if st == "restored":
            notify_admins(f"♻️ <b>GitHub থেকে ডাটা রিস্টোর হয়েছে</b>\n<blockquote>{esc(_gh_restore_info['msg'])}\n"
                          "প্রজেক্টের প্যাকেজগুলো চালু হওয়ার সময় আবার ইন্সটল হবে।</blockquote>")
        elif st == "failed":
            notify_admins("🚨 <b>GitHub থেকে রিস্টোর ব্যর্থ!</b>\n"
                          f"<blockquote>{esc(_gh_restore_info['msg'])}</blockquote>\n"
                          "পুরনো ব্যাকআপ নষ্ট না করতে নতুন ব্যাকআপ পুশ বন্ধ রাখা হয়েছে। সমস্যা ঠিক করে বট রিস্টার্ট দিন।")
        elif st == "empty_repo":
            notify_admins("ℹ️ GitHub ডাটা-রিপো খালি — নতুন করে শুরু হলো, ব্যাকআপ শুরু হবে।")
    except Exception:  # noqa
        pass


@bot.message_handler(commands=["ghbackup"])
def cmd_ghbackup(m):
    """Owner: /ghbackup -> back up to GitHub right now."""
    if not is_owner(m.from_user.id):
        return
    if not _gh_enabled():
        return bot.reply_to(m, "❌ <code>.env</code>-এ <code>DATA_REPO</code> ও <code>DATA_REPO_TOKEN</code> দিন।")
    bot.reply_to(m, "☁️ GitHub-এ ব্যাকআপ নেওয়া হচ্ছে…")

    def job():
        ok, msg = gh_backup(force=True)
        _gh_record(ok, msg)
        bot.send_message(m.chat.id, ("" if ok else "❌ ") + esc(msg))
    threading.Thread(target=job, daemon=True, name="ghbackup-now").start()


@bot.message_handler(commands=["ghstatus"])
def cmd_ghstatus(m):
    if not is_owner(m.from_user.id):
        return
    if not _gh_enabled():
        return bot.reply_to(m, "☁️ GitHub ব্যাকআপ বন্ধ — <code>DATA_REPO</code> ও <code>DATA_REPO_TOKEN</code> সেট করুন।")
    t = fmt_ts(_gh_status["time"]) if _gh_status["time"] else "—"
    bot.reply_to(m, head("☁️", "GitHub ব্যাকআপ", f"{esc(_gh_slug())} • {esc(GH_BRANCH)}")
                 + f"<blockquote>🕒 শেষ চেষ্টা: {t}\n{'✅' if _gh_status['ok'] else '⚠️'} {esc(_gh_status['msg'])}\n"
                   f"⏱ প্রতি {GH_MINUTES} মিনিটে অটো ব্যাকআপ\n"
                   f"{'🛑 পুশ বন্ধ (রিস্টোর ব্যর্থ)' if _gh_block_push else '🟢 পুশ চালু'}</blockquote>\n"
                   "এখনই ব্যাকআপ: /ghbackup")


def _gh_install_sigterm():
    """Railway sends SIGTERM on redeploy: try one last backup (max ~25s), then exit."""
    if not _gh_enabled():
        return

    def handler(signum, frame):
        def last():
            try:
                gh_backup(force=True)
            except Exception:  # noqa
                pass
        t = threading.Thread(target=last, daemon=True)
        t.start()
        t.join(25)
        os._exit(0)
    try:
        signal.signal(signal.SIGTERM, handler)
    except Exception:  # noqa
        pass


def start_background():
    threading.Thread(target=_expiry_loop, daemon=True, name="expiry").start()
    threading.Thread(target=_db_backup_loop, daemon=True, name="dbbackup").start()
    threading.Thread(target=_data_backup_loop, daemon=True, name="databackup").start()
    if _gh_enabled():
        threading.Thread(target=_gh_backup_loop, daemon=True, name="ghbackup").start()
        threading.Thread(target=_gh_startup_notice, daemon=True, name="ghnotice").start()
        _gh_install_sigterm()
        log.info("GitHub backup ON -> %s (every %s min)", _gh_slug(), GH_MINUTES)


# ==============================================================================
# 14. MAIN
# ==============================================================================
def main():
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS সেট করা নেই — অ্যাডমিন প্যানেল কেউ ব্যবহার করতে পারবে না!")
    init_permissions()
    restore_projects()
    start_monitor()
    start_background()
    log.info("Bot started as @%s", bot_username())
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
