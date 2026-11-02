"""群友让小王到点提醒（2026-11-02，见 docs/reminders.md）。

记不记、记什么由小王在回复里写〔提醒 …〕〔取消提醒〕决定（expression.py 认标记，提示词见
prompts.PROMPT_REMIND_RULES）；这里管时间怎么算、一人一条、记下以后小王该知道什么。

提醒就挂在 scheduler 上（kind="reminder"），一个人在一个群里只有一个固定 id：再定一条就是
改期，旧的被顶掉；取消就是撤掉这条任务。到点怎么发、发不出去怎么办在 bot.fire_reminder。
"""
import re
import time

KIND = "reminder"
MIN_LEAD_SECONDS = 60.0         # 至少一分钟以后：再近就不叫提醒了
MAX_DAYS = 30                   # 最远三十天：再远群里早没人记得这回事
GRACE_SECONDS = 3600.0          # 到点后一小时内还送（主动发不出去就等群里下一条消息捎带），过了作罢
_WEEKDAYS = "一二三四五六日"

# 小王写在标记里的时间。这是给它定的格式，不是去猜自然语言：「11-03 09:00」「2026-11-03 09:00」
# 「每天 09:00」，只写「09:00」算接下来的那个 09:00。
_WHEN = re.compile(
    r"^(?:(?P<daily>每天)|(?:(?P<y>\d{4})[-/.年])?(?P<m>\d{1,2})[-/.月](?P<d>\d{1,2})日?)?"
    r"\s*(?P<H>\d{1,2})[:：](?P<M>\d{2})$")


class Rejected(ValueError):
    """这条提醒记不了；args[0] 是说给群友听的原因。"""


def task_id(group, member):
    return f"{KIND}:{group}:{member}"


def _at(year, month, day, hour, minute):
    """本地时间 → 时间戳；日期不存在（2 月 30 日）就抛 Rejected。"""
    stamp = time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))
    if time.localtime(stamp)[:5] != (year, month, day, hour, minute):
        raise Rejected("这个日子不存在")
    return stamp


def _next_daily(hour, minute, after):
    """after 之后（留够 MIN_LEAD_SECONDS）的第一个 hour:minute。"""
    t = time.localtime(after + MIN_LEAD_SECONDS)
    due = _at(t.tm_year, t.tm_mon, t.tm_mday, hour, minute)
    while due < after + MIN_LEAD_SECONDS:
        t = time.localtime(due + 86400 + 3600)   # 跨一天，多加一小时躲开夏令时
        due = _at(t.tm_year, t.tm_mon, t.tm_mday, hour, minute)
    return due


def resolve(when, now):
    """标记里的时间 → (到点时间戳, 是否每天, 时:分)。记不了就抛 Rejected。"""
    m = _WHEN.match((when or "").strip())
    if not m:
        raise Rejected("时间没看懂")
    hour, minute = int(m["H"]), int(m["M"])
    if hour > 23 or minute > 59:
        raise Rejected("时间没看懂")
    hm = f"{hour:02d}:{minute:02d}"
    if m["daily"] or not m["m"]:
        return _next_daily(hour, minute, now), bool(m["daily"]), hm
    year = int(m["y"]) if m["y"] else time.localtime(now).tm_year
    try:
        due = _at(year, int(m["m"]), int(m["d"]), hour, minute)
    except (OverflowError, ValueError) as exc:
        raise Rejected(exc.args[0] if isinstance(exc, Rejected) else "时间没看懂") from None
    if not m["y"] and due < now + MIN_LEAD_SECONDS:
        # 没写年份、日子已经过了：年底定明年一月那种才往后挪一年，其余就是过了
        try:
            nxt = _at(year + 1, int(m["m"]), int(m["d"]), hour, minute)
        except Rejected:
            nxt = None
        if nxt is not None and nxt - now <= MAX_DAYS * 86400:
            due = nxt
    if due < now + MIN_LEAD_SECONDS:
        raise Rejected("这个时间已经过了")
    if due - now > MAX_DAYS * 86400:
        raise Rejected(f"太远了，只记{MAX_DAYS}天以内的")
    return due, False, hm


def spell_out(what, who):
    """到点前把「TA」换成让它提醒的那个人：规则里让小王这么写（PROMPT_REMIND_RULES），
    原样交给到点那次生成，它会把「TA」当名字念出来（luna 实测 3 次里 3 次）。"""
    return re.sub(r"(?<![A-Za-z])\s*TA\s*(?![A-Za-z])", who, what)


def next_occurrence(payload, now):
    """每天的那种：下一次什么时候。一次性的返回 None。"""
    if not payload.get("daily"):
        return None
    hour, minute = (int(x) for x in payload["hm"].split(":"))
    return _next_daily(hour, minute, max(now, payload["at"]))


def describe(payload):
    """「11-03 09:00」或「每天 09:00」：说给小王和群友听的那个时间。"""
    if payload.get("daily"):
        return f"每天 {payload['hm']}"
    return time.strftime("%m-%d %H:%M", time.localtime(payload["at"]))


def now_line(now):
    t = time.localtime(now)
    return f"【现在】{time.strftime('%m-%d', t)} 周{_WEEKDAYS[t.tm_wday]} {time.strftime('%H:%M', t)}"


def render_note(task, now):
    """即时提示里的那两行：现在几点（它要照着算时间），TA 记着哪条（一个人只有一条）。"""
    lines = [now_line(now)]
    if task is not None:
        p = task["payload"]
        lines.append(f"【TA 让你记着的提醒】{describe(p)}：{p['what']}"
                     "（再定一条就顶掉这条）")
    return "\n".join(lines)


def apply(scheduler, group, member, remind, now, *, blocked=None):
    """照小王写的标记登记 / 改期 / 取消。返回 (记进会话的那半句, 要补在回复后面的话)。

    remind 是 expression 拆出来的 ("set", 时间, 提醒什么) 或 ("cancel", "", "")。
    blocked(text) -> bool：要提醒的事过不了敏感词就不记。补的那句：记上了就报记下的时间 ——
    它自己算的时间偶尔差一天（luna 实测「晚上十一点」4 次里 1 次记成明晚），也常不说具体几点，
    群友得能当场核对；没记上就说为什么，它已经用自己的话答应过了。
    """
    tid = task_id(group, member)
    old = scheduler.tasks.get(tid)
    if remind[0] == "cancel":
        if old is None:
            return "TA 本来就没有记着的提醒", ""
        scheduler.cancel(tid)
        return f"提醒取消了（原来是 {describe(old['payload'])}）", ""
    _, when, what = remind
    what = (what or "").strip()[:100]
    try:
        if not what:
            raise Rejected("没写提醒什么")
        if blocked is not None and blocked(what):
            raise Rejected("这个我不提醒")
        due, daily, hm = resolve(when, now)
    except Rejected as exc:
        return f"这个提醒没记上：{exc.args[0]}", f"（这个提醒没记上：{exc.args[0]}）"
    payload = {"member": member, "what": what, "at": due, "daily": daily, "hm": hm, "set_at": now}
    if scheduler.at(KIND, group, due, payload, task_id=tid) is None:
        return "这个提醒没记上：提醒太多了", "（这个提醒没记上：手上记的提醒太多了）"
    note = f"提醒记下了：{describe(payload)}"
    if old is not None:
        note += f"（原来那条 {describe(old['payload'])} 不提醒了）"
    return note, f"（记下了：{describe(payload)}）"
