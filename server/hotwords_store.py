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

    # 校对文件行 -> 纯内容（剥掉「讲话人N [时间]」/「真名 [时间]」前缀）
    corrected_contents = [_LABEL_RE.sub("", l).strip() for l in lines]
    corrected_contents = [c for c in corrected_contents if c]

    orig_texts = [s["text"] for s in orig_sentences]

    # 字符流对齐：两边各自拼接成纯文本流后整体 diff。
    # 旧版逐句贪心对齐在「docx 按说话人合段 vs result.json 逐句」粒度错配、
    # 或人工删句/合并段落时错位雪崩（实测 330 行仅 6 行匹配、候选归零）。
    # 字符流对齐对改名、删句、合并段、增删字词全部天然免疫；
    # 删除/插入产生 delete/insert opcode（不产出替换对），只有 replace 是候选。
    orig_stream = "".join(orig_texts)
    corr_stream = "".join(corrected_contents)

    pairs_counter = Counter()
    sm = SequenceMatcher(None, orig_stream, corr_stream, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        wrong, right = orig_stream[i1:i2], corr_stream[j1:j2]
        for w, r in _diff_pairs(orig_stream, corr_stream, i1, i2, j1, j2):
            pairs_counter[(w, r)] += 1

    candidates = []
    for (w, r), n in pairs_counter.most_common():
        confidence = "high" if (len(w) >= 2 and len(r) >= 2) else "review"
        candidates.append({"wrong": w, "right": r, "count": n,
                           "confidence": confidence})
    return candidates


def _diff_pairs(orig_s, corr_s, i1, i2, j1, j2):
    """从字符流的一个 replace 区段提取替换片段，过滤噪声。

    区段内做二级 diff，把「标点/语气词变化」和「真修正」拆开分别判定。"""
    sm = SequenceMatcher(None, orig_s[i1:i2], corr_s[j1:j2], autojunk=False)
    pairs = []
    for tag, oi1, oi2, oj1, oj2 in sm.get_opcodes():
        if tag != "replace":
            continue
        wrong = orig_s[i1 + oi1:i1 + oi2]
        right = corr_s[j1 + oj1:j1 + oj2]
        if not wrong or not right:
            continue
        if len(wrong) > 15 or len(right) > 15:
            continue  # 整句重写级别，不适合做热词
        # 含标点/空白的一律不算（多为断句调整的碎片）
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
