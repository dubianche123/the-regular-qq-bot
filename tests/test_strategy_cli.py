"""本地策略评测的 CLI 授权、完整指标与维护面板边界。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import panel
from regular import cases, cli
from regular import eval as eval_mod


REPO = Path(__file__).resolve().parents[1]


def _config(root):
    data = root / "data"
    return SimpleNamespace(
        DATA_DIR=str(data), CASES_DIR=str(data / "cases"), EVAL_DIR=str(data / "evals"),
        TURN_LOG_DIR=str(data / "turns"), ARCHIVE_DIR=str(data / "archive"),
        CHECKUP_DIR=str(data / "checkups"), INTERACTION_LOG_DIR=str(data / "interactions"),
        STATE_FILE=str(data / "state.json"), POLICY_DIR=str(data / "policies"),
        CHECKUP_POLICY_FILE=str(data / "policies/checkup.json"),
        RECALL_POLICY_FILE=str(data / "policies/recall.json"),
    )


def _complete_case():
    now = 1793600000.0
    return {
        "schema": cases.SCHEMA, "id": "local-complete", "depth": "retrieve", "tags": [],
        "retrieve": {
            "query": "之前说的露营天幕呢", "names": [], "now": now, "promises": [],
            "exclude_text": "之前说的露营天幕呢",
            "coverage": {"complete": True, "reason": "合成的完整小语料"},
            "rows": [
                {"message_id": "camp", "sender": "u1", "text": "露营天幕放在储藏室了", "ts": now - 3600},
                {"message_id": "noise", "sender": "u2", "text": "今天午饭吃得挺好", "ts": now - 1800},
            ],
        },
        "retrieval_labels": {"complete": True, "relevant_ids": ["camp"]},
        "checks": [{"retrieved": "camp"}, {"not_retrieved": "noise"}],
    }


def test_retrieve_cli_works_without_keys_or_network_in_real_workers(tmp_path):
    cfg = _config(tmp_path)
    case_dir = Path(cfg.CASES_DIR)
    case_dir.mkdir(parents=True)
    (case_dir / "local-complete.json").write_text(json.dumps(_complete_case()), encoding="utf-8")
    guard = tmp_path / "guard"
    guard.mkdir()
    marker = tmp_path / "network-guard-loaded"
    (guard / "sitecustomize.py").write_text(
        "import os, socket\n"
        "with open(os.environ['REGULAR_NETWORK_GUARD_MARKER'], 'a') as out:\n"
        "    out.write('loaded\\n')\n"
        "def blocked(*args, **kwargs):\n"
        "    raise AssertionError('network is forbidden in local retrieval evaluation')\n"
        "socket.socket.connect = blocked\n"
        "socket.socket.connect_ex = blocked\n"
        "socket.create_connection = blocked\n", encoding="utf-8")
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    env.update(PYTHONPATH=str(guard), REGULAR_NETWORK_GUARD_MARKER=str(marker))
    proc = subprocess.run(
        [sys.executable, "-m", "regular", "--directory", str(tmp_path), "eval",
         "--base", str(REPO), "--cand", str(REPO), "--cases", "local-complete",
         "-n", "4", "--no-write", "--json"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=40)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["totals"]["cases"] == 1
    assert result["totals"]["regressed"] == 0
    matrix = result["totals"]["retrieval"]
    assert matrix["complete_cases"] == 1
    assert matrix["base"]["tp"] == matrix["cand"]["tp"] == 1
    assert matrix["base"]["fp"] == matrix["cand"]["fn"] == 0
    assert matrix["base"]["tn"] == matrix["cand"]["tn"] == 1
    assert len(marker.read_text().splitlines()) >= 3  # CLI 和两个真实回放 worker 都禁网。
    assert not Path(cfg.EVAL_DIR).exists()


@pytest.mark.parametrize("selected,extra", [
    ([{"depth": "generate", "checks": []}], []),
    ([{"depth": "triage", "checks": []}], []),
    ([{"depth": "message", "checks": []}], []),
    ([{"depth": "retrieve", "checks": []}, {"depth": "generate", "checks": []}], []),
    ([{"depth": "retrieve", "checks": [{"judge": "哪条更相关"}]}], []),
    ([], ["--day", "2026-11-03"]),
    ([], ["--search-wording", "remind"]),
])
def test_model_eval_still_requires_explicit_paid_authorization(tmp_path, monkeypatch, capsys, selected, extra):
    monkeypatch.setattr(cli, "load_config", lambda directory: _config(tmp_path))
    monkeypatch.setattr(cases, "load_cases", lambda *args, **kwargs: selected)

    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized evaluation reached a worker or a judge")

    monkeypatch.setattr(eval_mod, "preflight_judge", forbidden)
    monkeypatch.setattr(eval_mod, "run_worker_subprocess", forbidden)
    monkeypatch.setattr(eval_mod, "search_wording", forbidden)
    with pytest.raises(SystemExit) as error:
        cli.main(["eval", "--base", ".", "--cand", ".", "--no-write", *extra])
    assert error.value.code == 2
    assert "--allow-paid" in capsys.readouterr().err


def test_retrieval_matrix_detects_extra_false_positive_without_multiplying_samples():
    case = _complete_case()
    base = json.dumps({"picked": ["camp"], "promise": None})
    cand = json.dumps({"picked": ["camp", "noise"], "promise": None})
    # 仅正例检查照样通过，但完整标注还能发现多捞的无关消息。
    case["checks"] = [{"retrieved": "camp"}]
    rows, totals = asyncio.run(eval_mod.evaluate_cases(
        [case], [{"id": case["id"], "samples": [base] * 4}],
        [{"id": case["id"], "samples": [cand] * 4}]))
    assert rows[0]["base_pass"] == rows[0]["cand_pass"] == "4/4"
    assert rows[0]["regressed"]
    assert any("误报增加" in reason for reason in rows[0]["failed_reasons"])
    assert totals["retrieval"]["base"]["tp"] == 1
    assert totals["retrieval"]["cand"]["fp"] == 1


def test_partial_retrieval_checks_do_not_claim_recall():
    case = _complete_case()
    case.pop("retrieval_labels")
    sample = json.dumps({"picked": ["camp"], "promise": None})
    rows, totals = asyncio.run(eval_mod.evaluate_cases(
        [case], [{"id": case["id"], "samples": [sample]}],
        [{"id": case["id"], "samples": [sample]}]))
    assert not rows[0]["retrieval"]["base"]["evidence_complete"]
    assert rows[0]["retrieval"]["base"]["recall"] is None
    assert totals["retrieval"]["complete_cases"] == 0
    assert totals["retrieval"]["base"] is None


@pytest.mark.parametrize("args", [
    ["tune", "recall", "--benchmark", "data.json"],
    ["tune", "recall", "--folds", "1"],
    ["tune", "recall", "--drift-days", "-1"],
    ["tune", "checkup", "--min-labels", "0"],
    ["tune", "checkup", "--min-no", "-1"],
])
def test_invalid_tuning_arguments_are_rejected(tmp_path, monkeypatch, args):
    monkeypatch.setattr(cli, "load_config", lambda directory: _config(tmp_path))
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert not Path(_config(tmp_path).EVAL_DIR).exists()


def test_bad_panel_policy_warns_without_overwriting_existing_report(tmp_path):
    cfg = _config(tmp_path)
    policy_path = Path(cfg.CHECKUP_POLICY_FILE)
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text('{"policy": {"reaction_seconds": 9999}}', encoding="utf-8")
    reports = Path(cfg.CHECKUP_DIR)
    reports.mkdir(parents=True)
    report = reports / "2026-11-03.json"
    original = '{"day": "2026-11-03", "findings": [], "keep": "original"}'
    report.write_text(original, encoding="utf-8")
    html = panel._checkup(cfg, SimpleNamespace(query={"day": "2026-11-03"}), "csrf")
    assert "体检策略文件无效" in html
    assert "已有报告保持不变" in html
    assert "候选标注确认率" in html
    assert report.read_text(encoding="utf-8") == original


def test_cli_checkup_benchmark_reports_train_and_independent_validation(tmp_path, monkeypatch, capsys):
    from regular import tune

    samples = []
    for index, split in enumerate(("train", "validation")):
        day = f"2026-11-0{index + 2}"
        now = 1793600000.0 + index * 86400
        tp_id, fp_id = f"{split}-yes", f"{split}-no"
        samples.append({
            "id": split, "split": split, "day": day, "now": now + 60,
            "signal": "bad_reaction", "group": "group", "complete": True,
            "data": {
                "delivery": [
                    {"group": "group", "ts": now, "text": "上一句回复", "outgoing_id": split},
                    {"group": "group", "ts": now + 30, "text": "另一个正常回复", "outgoing_id": f"{split}-next"},
                ],
                "incoming": [
                    {"group": "group", "user": "user", "ts": now + 10,
                     "text": "你这答非所问啊", "message_id": tp_id},
                    {"group": "group", "user": "user", "ts": now + 40,
                     "text": "那个单词什么意思", "message_id": fp_id},
                ],
            },
            "baseline": {},
            "labels": [{"anchor": tp_id, "positive": True}, {"anchor": fp_id, "positive": False}],
        })
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(json.dumps({"schema": tune.CHECKUP_BENCHMARK_SCHEMA, "samples": samples}), encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda directory: _config(tmp_path))
    assert cli.main(["tune", "checkup", "--benchmark", str(benchmark), "--no-write", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scope"] == "independent_benchmark"
    for split in ("train", "validation"):
        before = result["benchmark"]["splits"][split]["base"]
        after = result["benchmark"]["splits"][split]["cand"]
        assert before["tp"] == after["tp"] == 1
        assert before["fp"] == 1 and after["fp"] == 0
        assert before["fn"] == after["fn"] == 0
        assert after["recall"] == 1.0
    assert not Path(_config(tmp_path).EVAL_DIR).exists()


def test_cli_case_add_explicit_complete_relevance(tmp_path, monkeypatch, capsys):
    import turns

    cfg = _config(tmp_path)
    day = "2026-11-02"
    spec = _complete_case()["retrieve"]
    archive_dir = Path(cfg.ARCHIVE_DIR)
    archive_dir.mkdir(parents=True)
    (archive_dir / f"{day}.jsonl").write_text(
        "".join(json.dumps({**row, "group": "group"}) + "\n" for row in spec["rows"]), encoding="utf-8")
    turn_dir = Path(cfg.TURN_LOG_DIR)
    turn_dir.mkdir(parents=True)
    turn = {"schema": turns.SCHEMA, "turn_id": "retrieval-source", "kind": "recall",
            "ts": spec["now"], "now": spec["now"], "group": "group", "user": "user",
            "query": spec["query"], "names": [], "picked": [], "promises": [],
            "promises_complete": True}
    (turn_dir / f"{day}.jsonl").write_text(json.dumps(turn) + "\n", encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda directory: cfg)
    assert cli.main(["case", "add", "--turn", "retrieval-source", "--id", "complete-from-cli",
                     "--relevant", "camp", "--complete-relevance"]) == 0
    capsys.readouterr()
    result = json.loads((Path(cfg.CASES_DIR) / "complete-from-cli.json").read_text(encoding="utf-8"))
    assert result["retrieval_labels"] == {"complete": True, "relevant_ids": ["camp"]}
    assert {row["message_id"] for row in result["retrieve"]["rows"]} == {"camp", "noise"}
    assert result["retrieve"]["coverage"]["complete"]


@pytest.mark.parametrize("document", [[], {"proposal": {"policy": {}}}])
def test_bad_policy_document_is_a_controlled_cli_error(tmp_path, monkeypatch, capsys, document):
    report = tmp_path / "bad-report.json"
    report.write_text(json.dumps(document), encoding="utf-8")
    cfg = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda directory: cfg)
    assert cli.main(["policy", "apply", str(report)]) == 1
    assert "Traceback" not in capsys.readouterr().err
    assert not Path(cfg.POLICY_DIR).exists()


@pytest.mark.parametrize("extra", [
    ["--expect-none", "--relevant", "camp"],
    ["--max-picked", "-1"],
])
def test_contradictory_or_negative_retrieval_expectations_are_rejected(tmp_path, monkeypatch, extra):
    cfg = _config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda directory: cfg)
    assert cli.main(["case", "add", "--turn", "source", "--id", "bad-case", *extra]) == 1
    assert not Path(cfg.CASES_DIR).exists()
