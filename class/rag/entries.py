"""색인 조각 글을 용어 이름·설명으로 읽고, 병명인지와 짧은 설명을 가른다."""
import re

from .constants import (
    CHEST_PHRASES,
    DISEASE_MARKERS,
    ENTRY_KEYS,
    NOT_DISEASE,
    REGION_NAMES,
)


def parse_entry(text: str) -> dict[str, str]:
    """한 행의 조각 글을 kr, en, definition, definition_kr 칸으로 나눈다."""
    nl = "\n"
    for key in ENTRY_KEYS:
        text = text.replace(" " + key + ":", nl + key + ":")
    fields: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.split(nl):
        matched = None
        rest = ""
        for key in ENTRY_KEYS:
            prefix = key + ":"
            if line.startswith(prefix):
                matched = key
                rest = line[len(prefix):].strip()
                break
        if matched:
            if current is not None:
                fields[current] = " ".join(buf).strip()
            current = matched
            buf = [rest]
        elif current is not None and line.strip():
            buf.append(line.strip())
    if current is not None:
        fields[current] = " ".join(buf).strip()
    return fields


def entry_record(parts: list[str]) -> dict | None:
    """한 행에서 한글·영어 이름과 설명을 꺼낸다. 이름이나 설명이 없으면 None 이다."""
    if not parts:
        return None
    fields = parse_entry("\n".join(parts))
    name_ko = (fields.get("kr") or "").strip()
    name_en = (fields.get("en") or "").strip()
    definition_ko = (fields.get("definition_kr") or "").strip()
    definition_en = (fields.get("definition") or "").strip()
    name = name_ko or name_en
    explanation = definition_ko or definition_en
    if not name or not explanation:
        return None
    return {
        "name": name,
        "name_en": name_en or None,
        "explanation": explanation,
        "definition_en": definition_en or None,
    }


def entry_from_parts(parts: list[str]) -> tuple[str | None, str | None]:
    """한 행의 조각들을 이어 검색에 쓰는 이름과 설명을 꺼낸다. 칸이 없으면 None 이다."""
    record = entry_record(parts)
    if not record:
        return None, None
    return record["name"], record["explanation"]


def name_aliases(name: str) -> list[str]:
    return [part.strip() for part in name.replace(";", ",").split(",") if part.strip()]


def is_disease(name: str) -> bool:
    """이름 별칭 중 하나라도 병명 표식이 있으면 병으로 친다. 부위 이름·검사·시술 이름은 빼 둔다."""
    for alias in name_aliases(name):
        if alias in REGION_NAMES:
            continue
        if any(word in alias for word in NOT_DISEASE):
            continue
        if alias.endswith("학") or "술" in alias:
            continue
        if any(marker in alias for marker in DISEASE_MARKERS):
            return True
        if len(alias) > 1 and alias.endswith(("증", "염", "암", "병", "종")):
            return True
        if len(alias) > 1 and alias.endswith("흉") and "개" not in alias:
            return True
        if (
            len(alias) > 2
            and alias.endswith("가슴")
            and not any(word in alias for word in ("근", "신경", "동맥", "막", "문", "봉", "띠", "빗장", "쇄골", "큰"))
        ):
            return True
    return False


def mention_text(explanation: str) -> str:
    """설명에서 흉부 촬영 이름을 빼, 그 말만으로 검색어가 맞은 것처럼 세지 않게 한다."""
    text = explanation
    for phrase in CHEST_PHRASES:
        text = text.replace(phrase, " ")
    return text


def hangul_count(text: str) -> int:
    return sum("가" <= ch <= "힣" for ch in text)


def short_explanation(text: str) -> str:
    """영문 괄호를 빼고, 한글 문장 둘까지만 남긴다."""

    def drop_foreign(match: re.Match[str]) -> str:
        inside = match.group(1)
        letters = sum(ch.isascii() and ch.isalpha() for ch in inside)
        hangul = sum("가" <= ch <= "힣" for ch in inside)
        if letters > hangul:
            return " "
        return match.group(0)

    text = re.sub(r"\(([^)]*)\)", drop_foreign, text)
    text = re.sub(r"\s+", " ", text).strip()
    pieces = re.split(r"(?<=다\.)\s+", text)
    kept: list[str] = []
    for piece in pieces:
        piece = piece.strip(" ;")
        if hangul_count(piece) < 6:
            continue
        if len(piece) > 180:
            piece = piece[:180].rstrip(" ,;.") + "."
        kept.append(piece)
        if len(kept) == 2:
            break
    if not kept:
        return text[:160]
    return " ".join(kept)


def short_english(text: str) -> str:
    """영어 설명을 문장 단위로 남긴다. 문장 중간에서 자르지 않는다."""
    text = re.sub(r"\s+", " ", text).strip()
    pieces = [piece.strip(" ;") for piece in re.split(r"(?<=\.)\s+", text)]
    pieces = [piece for piece in pieces if piece]
    if not pieces:
        return text[:160]
    kept: list[str] = []
    total = 0
    for piece in pieces:
        if not kept and len(piece) > 600:
            cut = piece[:600].rsplit(" ", 1)[0].rstrip(" ,;")
            return (cut or piece[:600]) + "…"
        if kept and total + len(piece) > 600:
            break
        kept.append(piece)
        total += len(piece) + 1
        if len(kept) == 3:
            break
    return " ".join(kept)
