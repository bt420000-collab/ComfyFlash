"""Read the official H3 writing Skill and a scene reference from the local library."""

from contextlib import closing
from pathlib import Path, PurePosixPath
import re
import sqlite3


DEFAULT_DB = Path(__file__).resolve().parent / "skills" / "h3vmaster.sqlite"
OFFICIAL_SKILL = "h3-prompt-writing/SKILL.md"
BASE_GUIDE = "h3-prompt-writing/references/base-en.txt"
REFERENCE_GUIDE = "h3-prompt-writing/references/ref-en.txt"
MODE_FILES = {
    "t1": BASE_GUIDE,
    "t2": REFERENCE_GUIDE,
    "t5": BASE_GUIDE,
    "t6": REFERENCE_GUIDE,
}
SKILL_LIMIT = 4000
MODE_LIMIT = 3200
SCENE_LIMIT = 2400
WORDS = re.compile(r"[^\W_]+")
ASCII_WORDS = re.compile(r"[a-z0-9]+")
STOP_WORDS = {"a", "an", "and", "for", "in", "no", "of", "on", "or", "the", "to", "with"}


def _matching_skills(db, query, entry):
    if not (entry or query):
        return []
    columns = "entry_path, name, substr(description, 1, 2000)"
    select = f"SELECT {columns} FROM skills"
    categorized = any(row[1] == "medium" for row in db.execute("PRAGMA table_info(skills)"))
    if entry:
        condition = "entry_path = ? AND medium = 'video'" if categorized else "entry_path = ?"
        row = db.execute(f"{select} WHERE {condition}", (entry,)).fetchone()
        if row is None:
            raise ValueError(f"找不到视频技能条目：{entry}")
        return [row]

    terms = set(WORDS.findall(query.casefold()[:300])) - STOP_WORDS
    ascii_terms = {term for term in terms if term.isascii()}
    other_terms = terms - ascii_terms
    matches = []
    if categorized:
        select += " WHERE medium = 'video'"
    for path, name, description in db.execute(select):
        title = (name or "").casefold()
        synopsis = (description or "").casefold()
        title_matches = ascii_terms.intersection(ASCII_WORDS.findall(title)) | {term for term in other_terms if term in title}
        synopsis_matches = ascii_terms.intersection(ASCII_WORDS.findall(synopsis)) | {term for term in other_terms if term in synopsis}
        total_matches = len(title_matches | synopsis_matches)
        if total_matches:
            matches.append((total_matches, len(title_matches), path, name, description))
    matches.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [row[2:] for row in matches[:3]]


def _scene_file(db, entry, tone):
    """Prefer a writing template or example to the skill's general entry point."""
    choices = []
    preferred, incompatible = {
        "t1": ("t2va", ("ref2va",)),
        "t5": ("i2va", ("t2va", "ref2va", "l2va")),
    }.get(tone, ("ref2va", ("t2va",)))
    for (path,) in db.execute(
        "SELECT path FROM text_files WHERE skill_entry_path = ? AND path LIKE '%/references/%'",
        (entry,),
    ):
        reference = PurePosixPath(path)
        name = reference.stem.casefold()
        if reference.suffix.casefold() not in {".md", ".txt"} or any(mode in name for mode in incompatible):
            continue
        score = (8 * (preferred in name) + 4 * ("prompt" in name)
                 + 2 * ("template" in name) + int("example" in name))
        if score:
            choices.append((-score, path))
    return min(choices)[1] if choices else entry


def _excerpt(db, path, scope, limit):
    sample = limit + 1 if limit is not None else None
    row = db.execute("SELECT substr(text, 1, coalesce(?, length(text))) FROM text_files WHERE path = ?",
                     (sample, path)).fetchone()
    if not row or not row[0]:
        raise ValueError(f"参考库缺少文本：{path}")
    text = row[0]
    return {"scope": scope, "path": path, "text": text[:limit],
            "truncated": limit is not None and len(text) > limit}


def read_pack(tone, *, db_path=None, query=None, entry=None, full_guide=False):
    path = Path(db_path or DEFAULT_DB).expanduser().resolve()
    result = {"tone": tone, "database": str(path), "available": path.is_file(), "references": [], "matches": []}
    if not result["available"]:
        return result

    mode_path = MODE_FILES[tone]
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        result["official_skill"] = _excerpt(db, OFFICIAL_SKILL, "skill", SKILL_LIMIT)
        result["references"].append(_excerpt(db, mode_path, "mode", None if full_guide else MODE_LIMIT))
        matches = _matching_skills(db, query, entry)
        result["matches"] = [{"entry_path": item[0], "name": item[1], "description": (item[2] or "")[:160]}
                             for item in matches]
        if matches:
            scene_path = _scene_file(db, matches[0][0], tone)
            if scene_path not in (mode_path, OFFICIAL_SKILL):
                result["references"].append(_excerpt(db, scene_path, "scene", SCENE_LIMIT))
    return result
