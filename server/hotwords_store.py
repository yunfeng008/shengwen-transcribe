#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""热词表管理 + 校对学习。

热词文件：assets/hotwords_<domain>.txt，每行一条「错误词=>正确词」，# 注释。
校对学习：用户把人工改好的 Word/txt 传回，与原始转写 diff，
          自动提取「错词=>对词」候选，确认后写入热词表。
"""

import re
import zipfile
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from xml.etree import ElementTree as ET

APP_ROOT = Path(__file__).parent.parent.resolve()
ASSETS = APP_ROOT / "assets"

DOMAINS = ("general", "religion", "meeting", "lecture")

def hotwords_path(domain):
    domain = domain if domain in DOMAINS else "general"
    return ASSETS / f"hotwords_{domain}.txt"


def read_hotwords(domain):
    """返回 [{wrong, right}] 保持文件顺序。"""
    path = hotwords_path(domain)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=>" not in line:
            continue
        wrong, right = line.split("=>", 1)
        wrong, right = wrong.strip(), right.strip()
        if wrong and right:
            out.append({"wrong": wrong, "right": right})
    return out


def add_hotword(domain, wrong, right):
    """添加一条；已存在（同 wrong）则更新 right。返回是否新增。"""
    path = hotwords_path(domain)
    if not path.exists():
        path.write_text(f"# {domain} 热词表\n\n", encoding="utf-8")
    existing = read_hotwords(domain)
    for item in existing:
        if item["wrong"] == wrong:
            if item["right"] == right:
                return False  # 无变化
            # 更新：重写文件
            lines = [f"{i['wrong']}=>{i['right']}" if i["wrong"] != wrong
                     else f"{wrong}=>{right}" for i in existing]
            _write_lines(path, lines)
            return False
    # 新增：追加到映射区末尾（先确保文件以换行结尾，防止与上一条粘连）
    with open(path, "rb") as f:
        f.seek(-1, 2)
        if f.read(1) != b"\n":
            with open(path, "a", encoding="utf-8") as af:
                af.write("\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{wrong}=>{right}\n")
    return True


def remove_hotword(domain, wrong):
    path = hotwords_path(domain)
    existing = read_hotwords(domain)
    kept = [i for i in existing if i["wrong"] != wrong]
    if len(kept) == len(existing):
        return False
    _write_lines(path, [f"{i['wrong']}=>{i['right']}" for i in kept])
    return True


def _write_lines(path, lines):
    body = "\n".join(lines) + "\n"
    # 保留原文件的注释头
    if path.exists():
        header = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#"):
                header.append(line)
            elif line.strip():
                break
        if header:
            body = "\n".join(header) + "\n\n" + body
    path.write_text(body, encoding="utf-8")


def import_batch(domain, text):
    """批量导入：每行「错误词=>正确词」或「错误词,正确词」。返回 (新增数, 更新数)。"""
    added, updated = 0, 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            wrong, right = line.split("=>", 1)
        elif "," in line:
            wrong, right = line.split(",", 1)
        elif "\t" in line:
            wrong, right = line.split("\t", 1)
        else:
            continue
        wrong, right = wrong.strip(), right.strip()
        if not wrong or not right:
            continue
        if add_hotword(domain, wrong, right):
            added += 1
        else:
            updated += 1
    return added, updated


# ---------------- 校对学习 ----------------

def _docx_to_lines(path):
    """读 docx，返回非空段落列表。"""
    with zipfile.ZipFile(path) as z:
        with z.open("word/document.xml") as f:
            tree = ET.parse(f)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    out = []
    for p in tree.iter(ns + "p"):
        t = "".join(x.text or "" for x in p.iter(ns + "t")).strip()
        if t:
            out.append(t)
    return out


def _txt_to_lines(path):
    return [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


# 行首的「名字/讲话人N  [时间]」或「讲话人N  时间」前缀
_LABEL_RE = re.compile(
    r"^(?:讲话人\d+|[^\[\]]{1,12}?)\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]|\d{1,2}:\d{2}(?::\d{2})?)\s*")


def extract_replacements(orig_sentences, corrected_file):
    """对比原始句子与人工校对文件，提取「错词=>对词」候选。

    orig_sentences: result.json 的 sentences（有 start/text）
    corrected_file: 用户改好的 .docx 或 .txt
    返回 candidates: [{wrong, right, count, confidence}] 按出现次数降序。
    confidence: "high"（多字词对，推荐）/ "review"（单字对，需人工确认）
    """
    corrected_file = Path(corrected_file)
    if corrected_file.suffix.lower() == ".docx":
        lines = _docx_to_lines(corrected_file)
    else:
        lines = _txt_to_lines(corrected_file)

    # 校对文件行 -> 纯内容（剥掉「讲话人N [时间]」前缀）
    corrected_contents = [_LABEL_RE.sub("", l).strip() for l in lines]
    corrected_contents = [c for c in corrected_contents if c]

    orig_texts = [s["text"] for s in orig_sentences]

    # 对齐：校对文件的内容行与原句，按顺序用相似度贪心对齐
    # （人工校对通常不增删句子，只改字词；顺序基本一致）
    pairs_counter = Counter()
    si = 0  # 原句游标
    for c in corrected_contents:
        # 在原句窗口内找最佳匹配
        best_j, best_sim = None, 0.0
        for j in range(si, min(si + 12, len(orig_texts))):
            sim = SequenceMatcher(None, orig_texts[j], c).ratio()
            if sim > best_sim:
                best_j, best_sim = j, sim
        if best_j is None or best_sim < 0.5:
            continue  # 对不上（可能是新增内容），跳过
        orig_t, corr_t = orig_texts[best_j], c
        si = best_j + 1

        # 同句：提取词级替换对
        for wrong, right in _diff_pairs(orig_t, corr_t):
            pairs_counter[(wrong, right)] += 1

    candidates = []
    for (w, r), n in pairs_counter.most_common():
        confidence = "high" if (len(w) >= 2 and len(r) >= 2) else "review"
        candidates.append({"wrong": w, "right": r, "count": n,
                           "confidence": confidence})
    return candidates


def _diff_pairs(orig_t, corr_t):
    """从一对句子里提取替换片段，过滤噪声。"""
    sm = SequenceMatcher(None, orig_t, corr_t)
    pairs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        wrong, right = orig_t[i1:i2], corr_t[j1:j2]
        if not wrong or not right:
            continue
        if len(wrong) > 15 or len(right) > 15:
            continue  # 整句重写级别，不适合做热词
        # 含标点/空白的一律不算（多为对齐错位的碎片）
        if re.search(r"[，。、？！；：\s「」（）]", wrong + right):
            continue
        # 纯数字差异不算（数字格式偏好，非识别错误）
        if wrong.isdigit() or right.isdigit():
            continue
        # 语气词差异不算
        if wrong in ("呃", "嗯", "啊") or right in ("呃", "嗯", "啊"):
            continue
        # 高频虚词不算（单字：的/了/是…；双字：这个/然后…，替换误伤率极高）
        FUNCTION_CHARS = set("的了是在有和就不人都一个也很到说要去你我这他她它们那个什么之与其")
        if (len(wrong) == 1 and wrong in FUNCTION_CHARS) or \
           (len(right) == 1 and right in FUNCTION_CHARS):
            continue
        FUNCTION_WORDS = {"这个", "那个", "然后", "就是", "但是", "所以", "我们",
                          "你们", "他们", "什么", "这个", "还是", "如果", "可以",
                          "一下", "一个", "这是", "这跟", "这样", "那么", "因为"}
        if wrong in FUNCTION_WORDS or right in FUNCTION_WORDS:
            continue
        pairs.append((wrong, right))
    return pairs
