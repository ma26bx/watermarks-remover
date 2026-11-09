"""Bounded, stdlib-only text reports and Unicode cleanup for small servers."""

import bisect
import re
import statistics
from collections import Counter

from score_stylometry import score_text_stylometry
from text_detectors import GumbelTextDetector
from text_unicode import clean_text, inspect_text

MAX_CHARS = 100_000
WORDS = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*", re.UNICODE)


def _prose(text):
    lines = []
    fence = None
    for line in text.splitlines():
        mark = re.match(r"^\s*(`{3,}|~{3,})", line)
        if mark:
            token = mark.group(1)
            if fence is None:
                fence = token[0]
            elif fence == token[0]:
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


def _frequent(counter, minimum=2):
    return [{"text": term, "count": count} for term, count in counter.most_common(10) if count >= minimum]


def analyze_text(text):
    if not isinstance(text, str):
        raise ValueError("text deve ser uma string")
    sample = text[:MAX_CHARS]
    prose = _prose(sample)
    tokens = [m.group().casefold() for m in WORDS.finditer(prose)]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", prose) if WORDS.search(s)]
    lengths = [len(WORDS.findall(s)) for s in sentences]
    counts = Counter(tokens)
    paragraphs = [p for p in re.split(r"\n\s*\n", prose) if WORDS.search(p)]
    repeated = []
    for size in (2, 3):
        phrases = Counter()
        # Never invent repeated phrases spanning unrelated sentences.
        for sentence in sentences:
            words = [m.group().casefold() for m in WORDS.finditer(sentence)]
            phrases.update(" ".join(words[i:i + size]) for i in range(len(words) - size + 1))
        repeated.extend({"words": size, **hit} for hit in _frequent(phrases))
    unicode_report = inspect_text(sample, aggressive=True).to_dict()
    newlines = [-1] + [i for i, ch in enumerate(sample) if ch == "\n"]
    hits = unicode_report["hits"]
    for hit in hits[:50]:
        hit["positions"] = [{"offset": offset, "line": bisect.bisect_left(newlines, offset),
                             "column": offset - newlines[bisect.bisect_left(newlines, offset) - 1]}
                            for offset in hit["sample_offsets"]]
    unicode_report["hit_types_total"] = len(hits)
    unicode_report["hits"] = hits[:50]
    unicode_report["offset_unit"] = "Unicode code points; line and column are 1-based"
    style = score_text_stylometry(sample).to_dict()
    for marker in style.get("matched_markers", []):
        marker["samples"] = marker.get("samples", [])[:3]
        marker["spans"] = marker.get("spans", [])[:10]
    style["interpretation"] = "Heurística de estilo, sem probabilidade de autoria. Marcadores de frases são principalmente em inglês e não têm calibração para português."
    gumbel = GumbelTextDetector().detect(sample)
    n = len(tokens)
    return {
        "ok": True,
        "scope": {"input_characters": len(text), "analyzed_characters": len(sample),
                  "truncated": len(text) > MAX_CHARS, "limit_characters": MAX_CHARS,
                  "prose_metrics_exclude_fenced_code": True},
        "counts": {"characters": len(sample), "words": n, "unique_words": len(counts),
                   "sentences": len(sentences), "paragraphs": len(paragraphs),
                   "nonempty_lines": sum(bool(line.strip()) for line in sample.splitlines())},
        "readability": {"mean_words_per_sentence": round(statistics.mean(lengths), 2) if lengths else None,
                        "median_words_per_sentence": statistics.median(lengths) if lengths else None,
                        "sentence_length_stddev": round(statistics.pstdev(lengths), 2) if len(lengths) >= 2 else None,
                        "long_sentence_threshold_words": 30,
                        "sentences_over_30_words": sum(length > 30 for length in lengths),
                        "mean_characters_per_word": round(sum(map(len, tokens)) / n, 2) if n else None,
                        "unique_word_ratio": round(len(counts) / n, 4) if n else None,
                        "estimated_reading_minutes_at_200_wpm": round(n / 200, 2),
                        "note": "Medidas descritivas aproximadas, sem nota escolar ou diagnóstico de dificuldade. A divisão de frases pode separar abreviações."},
        "repetitions": {"words": _frequent(counts), "phrases": repeated,
                        "note": "Contagens incluem palavras funcionais; repetição não implica marca-d’água."},
        "markdown": {"headings": len(re.findall(r"(?m)^ {0,3}#{1,6}\s", prose)),
                     "list_items": len(re.findall(r"(?m)^\s*(?:[-+*]|\d+[.)])\s+", prose))},
        "unicode": unicode_report, "stylometry": style, "gumbel": gumbel,
        "limitations": ["Não comprova autoria humana ou de IA nem ausência de toda marca-d’água.",
                        "Gumbel depende da chave, tokenização e implementação usadas na geração; não identifica marcas de qualquer fornecedor.",
                        "Texto colado não contém os metadados de um arquivo original."],
    }


def normalize_text(text, options=None):
    if not isinstance(text, str) or len(text) > MAX_CHARS:
        raise ValueError(f"text deve ser uma string de até {MAX_CHARS} caracteres; nada foi alterado")
    options = {} if options is None else options
    allowed = {"nfkc", "aggressive_homoglyphs", "normalize_spaces"}
    if not isinstance(options, dict) or set(options) - allowed or any(type(v) is not bool for v in options.values()):
        raise ValueError("Opções aceitas: nfkc, aggressive_homoglyphs e normalize_spaces, todas booleanas")
    cleaned, stats = clean_text(text, **options)
    return {"ok": True, "text": cleaned, "changed": cleaned != text, "changes": stats,
            "unicode_before": inspect_text(text).suspicious_total,
            "unicode_after": inspect_text(cleaned).suspicious_total,
            "note": "Limpeza Unicode sem reescrita. NFKC e troca de homoglifos só são aplicados quando solicitados. Não garante remoção de marcas estatísticas."}
