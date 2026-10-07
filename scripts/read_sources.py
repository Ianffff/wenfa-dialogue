#!/usr/bin/env python3
"""Read pinned canonical passages. Python standard library only; no network."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "references" / "canon"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class Corpus:
    def __init__(self):
        self.catalog = read_json(DATA / "catalog.json")
        self.chinese = read_json(DATA / "chinese.json")
        self.topics = read_json(DATA / "topics.json")["topics"]
        self.sources = {}
        self.texts = {}
        for source in self.catalog["sources"]:
            key = (source["sutta_id"], source["language"])
            if key in self.sources:
                raise ValueError(f"Duplicate source: {key}")
            self.sources[key] = source

    def source_path(self, source):
        path = (DATA / source["snapshot_path"]).resolve()
        if DATA.resolve() not in path.parents:
            raise ValueError("Snapshot path escapes corpus directory")
        return path

    def text(self, sutta, language):
        key = (sutta, language)
        if key not in self.sources:
            raise ValueError(f"Source not bundled: {sutta}/{language}")
        if key not in self.texts:
            source = self.sources[key]
            raw = self.source_path(source).read_bytes()
            if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError(f"Snapshot checksum mismatch: {sutta}/{language}")
            self.texts[key] = json.loads(raw)
        return self.texts[key]

    def passage(self, segment):
        sutta = segment.split(":", 1)[0]
        pali = self.text(sutta, "pli")
        english = self.text(sutta, "en")
        if segment not in pali:
            raise ValueError(f"Unknown segment: {segment}")
        cached = self.chinese["segments"].get(segment)
        if cached and (cached["pali"] != pali[segment]
                       or cached["english"] != english.get(segment)):
            raise ValueError(f"Chinese record has mismatched source text: {segment}")
        return {
            "segment_id": segment,
            "pali": pali[segment],
            "english": english.get(segment),
            "chinese": cached["chinese"] if cached else None,
            "translation_status": "editorial_working_translation" if cached else "not_cached",
            "note": cached.get("note", "") if cached else "",
            "urls": {lang: self.sources[(sutta, lang)]["canonical_url"]
                     + "#" + segment.split(":", 1)[1] for lang in ("pli", "en")},
        }

    def with_context(self, requested, size):
        found = []
        for segment in requested:
            self.passage(segment)  # Validate before finding neighbours.
            ids = list(self.text(segment.split(":", 1)[0], "pli"))
            index = ids.index(segment)
            found.extend(ids[max(0, index - size):index + size + 1])
        return [self.passage(s) for s in dict.fromkeys(found)]

    def verify(self):
        errors = []
        source_count = 0
        for (sutta, language), source in self.sources.items():
            try:
                text = self.text(sutta, language)
                if not isinstance(text, dict) or not all(
                    k.startswith(sutta + ":") and isinstance(v, str)
                    for k, v in text.items()
                ):
                    raise ValueError(f"Invalid segment map: {sutta}/{language}")
                if source["repository_commit"] != self.catalog["repository_commit"]:
                    raise ValueError(f"Commit mismatch: {sutta}/{language}")
                source_count += 1
            except (ValueError, OSError, KeyError) as error:
                errors.append(str(error))
        sutttas = sorted({s for s, _ in self.sources})
        for sutta in sutttas:
            for lang in ("pli", "en"):
                if (sutta, lang) not in self.sources:
                    errors.append(f"Missing parallel source: {sutta}/{lang}")
        for segment, cached in self.chinese["segments"].items():
            try:
                self.passage(segment)
                sutta = segment.split(":", 1)[0]
                if cached["sutta_id"] != sutta:
                    raise ValueError(f"Chinese sutta mismatch: {segment}")
                for suffix, lang in (("root-pli-ms", "pli"), ("translation-en-sujato", "en")):
                    if cached["source_sha256"][suffix] != self.sources[(sutta, lang)]["sha256"]:
                        raise ValueError(f"Chinese source checksum mismatch: {segment}/{lang}")
            except (ValueError, OSError, KeyError) as error:
                errors.append(str(error))
        topic_ids = [t["id"] for t in self.topics]
        if len(set(topic_ids)) != len(topic_ids):
            errors.append("Duplicate topic IDs")
        for topic in self.topics:
            for field in ("segment_ids", "focus_ids", "speaker_evidence"):
                for segment in topic[field]:
                    try:
                        self.passage(segment)
                        if not segment.startswith(topic["sutta_id"] + ":"):
                            raise ValueError(f"Topic sutta mismatch: {topic['id']}/{segment}")
                        if segment not in topic["segment_ids"]:
                            raise ValueError(f"Topic reference outside segments: {topic['id']}/{segment}")
                    except (ValueError, OSError, KeyError) as error:
                        errors.append(str(error))
        for topic, segments in self.chinese["reading_excerpts"].items():
            if topic not in topic_ids:
                errors.append(f"Unknown Chinese excerpt topic: {topic}")
            for segment in segments:
                if segment not in self.chinese["segments"]:
                    errors.append(f"Missing Chinese excerpt: {segment}")
        return {
            "ok": not errors, "sources_verified": source_count,
            "suttas": len(sutttas), "topics": len(topic_ids),
            "chinese_segments": len(self.chinese["segments"]),
            "errors": sorted(set(errors)),
            "scope": "File integrity and reference mappings only; not semantic or translation certification.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List topic navigation and complete source inventory")
    commands.add_parser("verify", help="Check source hashes, translations' source text and mappings")
    topic_parser = commands.add_parser("topic", help="Read topic focus passages and speaker context")
    topic_parser.add_argument("id")
    topic_parser.add_argument("--all", action="store_true", help="Read all navigation segments")
    passage_parser = commands.add_parser("passage", help="Read exact segment IDs")
    passage_parser.add_argument("ids", nargs="+")
    passage_parser.add_argument("--context", type=int, choices=range(0, 6), default=0)
    search_parser = commands.add_parser("search", help="Literal search, not exhaustive semantic retrieval")
    search_parser.add_argument("query")
    search_parser.add_argument("--sutta")
    search_parser.add_argument("--limit", type=int, choices=range(1, 51), default=10)
    args = parser.parse_args()
    try:
        corpus = Corpus()
        if args.command == "list":
            result = {"topics": [{k: t[k] for k in ("id", "title", "sutta_id")}
                                 for t in corpus.topics],
                      "suttas": sorted({s for s, _ in corpus.sources}),
                      "scope": "Navigation hints, not an answer whitelist or complete canon."}
        elif args.command == "verify":
            result = corpus.verify()
        elif args.command == "topic":
            topic = next((t for t in corpus.topics if t["id"] == args.id), None)
            if topic is None:
                raise ValueError("Unknown topic; use list or search the full source text.")
            field = "segment_ids" if args.all else "focus_ids"
            selected = set(topic[field] + topic["speaker_evidence"])
            ids = [s for s in corpus.text(topic["sutta_id"], "pli") if s in selected]
            result = {"topic": topic, "passages": [corpus.passage(s) for s in ids],
                      "selection": "all_navigation" if args.all else "focus_and_speaker_context",
                      "note": "Speaker hint is editorial; read context to verify each utterance."}
        elif args.command == "passage":
            result = {"passages": corpus.with_context(args.ids, args.context)}
        else:
            if not args.query.strip():
                raise ValueError("Search query must not be blank")
            sutttas = sorted({s for s, _ in corpus.sources})
            if args.sutta:
                if args.sutta not in sutttas:
                    raise ValueError("Sutta not bundled; use list to see scope.")
                sutttas = [args.sutta]
            matches = []
            query = args.query.casefold()
            for sutta in sutttas:
                for segment in corpus.text(sutta, "pli"):
                    row = corpus.passage(segment)
                    fields = [k for k in ("pali", "english", "chinese")
                              if query in (row[k] or "").casefold()]
                    if fields:
                        matches.append({"matched_fields": fields, **row})
            result = {"total_matches": len(matches), "shown": min(args.limit, len(matches)),
                      "truncated": len(matches) > args.limit, "matches": matches[:args.limit],
                      "note": "Literal search only; no matches does not establish absence from the canon."}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if args.command == "verify" and not result["ok"] else 0
    except (ValueError, KeyError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
