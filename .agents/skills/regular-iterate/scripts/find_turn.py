"""按一句原话找当轮记录：截图里群友或小王说过的话 → turn_id、message_id，好拿去定位和存用例。

用法（在仓库根目录跑）：
  .venv/bin/python .agents/skills/regular-iterate/scripts/find_turn.py "那只龙周一会装睡吗" --directory "$DEPLOY" [--day 11-05]

翻 data/turns（当轮原样输入：生成 reply / 分诊 triage / 旧账 recall）和 data/interactions（进来的话、送达的话），
只读，不联网。不给 --day 就从最近一天往回翻，找到为止（最多 7 天）。
"""
import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

import turns as turns_mod  # noqa: E402


def _day(value):
    if not value or value == "today":
        return dt.date.today().isoformat()
    if value == "yesterday":
        return (dt.date.today() - dt.timedelta(days=1)).isoformat()
    return value if len(value) == 10 else f"{dt.date.today().year}-{value}"


def _rows(path):
    for name, opener in ((path, open), (Path(f"{path}.gz"), gzip.open)):
        try:
            with opener(name, "rt", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except OSError:
            continue


def _clock(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts or 0))


def _flat(text, limit):
    text = " ".join(str(text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def search(data_dir, needle, day):
    records = [("turn", row) for row in turns_mod.read_day(data_dir / "turns", day)]
    records += [("interaction", row) for row in _rows(data_dir / "interactions" / f"{day}.jsonl")
                if row.get("kind") in ("incoming", "delivery")]
    links, keys, selected = {}, [], set()
    for i, (source, row) in enumerate(records):
        message_id = (row.get("source_message_id") if row.get("kind") == "delivery"
                      else row.get("message_id"))
        row_keys = []
        if message_id:
            row_keys.append(("message", row.get("group") or "", message_id))
        if row.get("turn_id"):
            row_keys.append(("turn", row["turn_id"]))
        keys.append(row_keys)
        for key in row_keys:
            links.setdefault(key, []).append(i)
        blob = (json.dumps({"kw": row.get("kwargs"), "raw": row.get("raw"),
                            "payload": row.get("payload")}, ensure_ascii=False)
                if source == "turn" else str(row.get("text") or ""))
        if needle in blob:
            selected.add(i)

    # 从原话命中开始，把同一条消息的分诊、生成与送达关联起来。
    # delivery 只有 turn_id 时，也能先找到生成，再关联回 incoming / triage。
    pending = sorted(selected)
    for i in pending:
        for key in keys[i]:
            for related in links[key]:
                if related not in selected:
                    selected.add(related)
                    pending.append(related)

    hits = []
    for i in sorted(selected):
        source, t = records[i]
        if source == "turn":
            kw = t.get("kwargs") or {}
            head = (f"turn {t.get('kind'):<7} {t.get('turn_id')}  msg={t.get('message_id') or '—'}"
                    f"  release={t.get('release') or '—'}")
            if t.get("kind") == "triage":
                body = f"分诊：问 {','.join(t.get('asked') or ())} → {json.dumps(t.get('choices') or {}, ensure_ascii=False)}"
            else:
                body = f"群友：{_flat(kw.get('user_text'), 60)}" + (
                    f"\n           小王：{_flat(t.get('raw'), 80)}" if t.get("raw") else "")
            hits.append((t.get("ts"), f"{head}\n           {body}"))
        else:
            r = t
            ref = r.get("message_id") if r["kind"] == "incoming" else f"src={r.get('source_message_id') or '—'}"
            state = (f" status={r.get('status') or '—'}"
                     + (f" reason={_flat(r['reason'], 60)}" if r.get("reason") else "")
                     if r["kind"] == "delivery" else "")
            hits.append((r.get("ts"), f"{r['kind']:<13} {ref}{state}  {_flat(r.get('text'), 70)}"))
    return sorted(hits, key=lambda h: h[0] or 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text", help="截图里的一句原话（取一段独特的就行）")
    parser.add_argument("--directory", default=os.environ.get("DEPLOY") or os.environ.get("REGULAR_HOME"),
                        help="部署目录（线上 .env 和 data/ 所在）；默认取 $DEPLOY 或 $REGULAR_HOME")
    parser.add_argument("--day", help="YYYY-MM-DD、MM-DD、today、yesterday；不给就从最近一天往回找")
    args = parser.parse_args(argv)
    if not args.directory:
        parser.error("给 --directory，或先 export DEPLOY=部署目录")
    data_dir = Path(args.directory).expanduser() / "data"
    days = [_day(args.day)] if args.day else [
        (dt.date.today() - dt.timedelta(days=i)).isoformat() for i in range(7)]
    for day in days:
        hits = search(data_dir, args.text, day)
        if hits:
            print(f"== {day}")
            for ts, line in hits:
                print(f"{_clock(ts)}  {line}")
            return 0
    print(f"没找到「{args.text}」（翻了 {', '.join(days)}）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
