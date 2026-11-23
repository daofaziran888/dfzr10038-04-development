"""质量放行接口集成测试：提交、存储清单、特征存储回写与阈值变更不改历史。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import store


@pytest.fixture(autouse=True)
def fresh_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# 三个验收分区：一个高于空值阈值、一个无样本、一个恰等于阈值。
ACCEPTANCE_PARTITIONS = [
    {"partition": "dt=2026-11-01", "denominator": 100, "null_count": 5},   # 恰好 5%
    {"partition": "dt=2026-11-02", "denominator": 0, "null_count": 0},     # 无样本
    {"partition": "dt=2026-11-03", "denominator": 100, "null_count": 6},   # 6% > 5%
]


def submit(client: TestClient, **overrides):
    payload = {
        "rule_no": "QUAL-0002",
        "rule_version": 1,
        "batch_no": "BATCH-20261103",
        "partitions": ACCEPTANCE_PARTITIONS,
    }
    payload.update(overrides)
    response = client.post("/api/quality-release/releases", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_acceptance_three_partitions_returns_readable_and_rejection_evidence(client):
    body = submit(client)
    assert body["ok"] is True
    release = body["release"]

    assert release["结论"] == "部分放行"
    # 恰好等于阈值的分区通过；超阈分区与空分区都被拒绝。
    assert release["可读分区"] == "dt=2026-11-01"
    assert set(release["阻断分区"].split(";")) == {"dt=2026-11-02", "dt=2026-11-03"}
    assert release["可读行数"] == 100
    assert release["抽样分母合计"] == 200
    assert release["覆盖率"] == "50.00%"

    partitions = {item["partition"]: item for item in release["分区结果"]}
    assert partitions["dt=2026-11-01"]["verdict"] == "通过"
    assert partitions["dt=2026-11-01"]["null_ratio_text"] == "5.00%"
    assert partitions["dt=2026-11-01"]["readable"] is True

    assert partitions["dt=2026-11-02"]["verdict"] == "拒绝"
    assert partitions["dt=2026-11-02"]["triggered"] == ["无样本"]
    assert partitions["dt=2026-11-02"]["null_ratio"] is None
    assert partitions["dt=2026-11-02"]["readable"] is False

    assert partitions["dt=2026-11-03"]["triggered"] == ["空值超阈"]
    assert partitions["dt=2026-11-03"]["null_ratio_text"] == "6.00%"
    assert partitions["dt=2026-11-03"]["readable"] is False

    # 拒绝证据能回到实际分区和字段。
    evidence = {item["partition"]: item for item in release["拒绝证据"]}
    assert set(evidence) == {"dt=2026-11-02", "dt=2026-11-03"}
    for item in evidence.values():
        assert item["field"] == "age"
        assert item["rule_no"] == "QUAL-0002"
        assert item["rule_version"] == 1
        assert item["reason"]


def test_manifest_lists_only_readable_partitions(client):
    release = submit(client)["release"]
    manifest = client.get(
        f"/api/quality-release/releases/{release['放行编号']}/manifest"
    ).json()
    assert manifest["readable_partitions"] == ["dt=2026-11-01"]
    assert set(manifest["blocked_partitions"]) == {"dt=2026-11-02", "dt=2026-11-03"}
    assert manifest["conclusion"] == "部分放行"
    assert manifest["store_write_status"] == "写入通过分区"
    statuses = {item["partition"]: item["readable"] for item in manifest["partition_status"]}
    assert statuses == {
        "dt=2026-11-01": True,
        "dt=2026-11-02": False,
        "dt=2026-11-03": False,
    }


def test_feature_store_only_gets_readable_partitions(client):
    submit(client)
    entry = next(
        row for row in store.rows("feature_store")
        if "user_age_7d" in str(row.get("特征列表", ""))
    )
    assert entry["可用分区"] == "dt=2026-11-01"
    assert set(entry["不可用分区"].split(";")) == {"dt=2026-11-02", "dt=2026-11-03"}
    assert entry["存储写入状态"] == "写入通过分区"


def test_batch_level_block_holds_everything(client):
    payload = ACCEPTANCE_PARTITIONS
    client.post(
        "/api/quality-release/rules",
        json={"rule_no": "QUAL-0002", "values": {"阻断级别": "阻断整批"}},
    )
    body = submit(client, partitions=payload, rule_version=None)
    release = body["release"]
    assert release["规则版本"] == 2
    assert release["结论"] == "整批扣住"
    assert release["可读分区"] == ""
    assert release["可读行数"] == 0
    assert release["扣留原因"]

    # 本地通过的分区也收回可读性，但保留原始判定供排查。
    first = next(p for p in release["分区结果"] if p["partition"] == "dt=2026-11-01")
    assert first["local_readable"] is True
    assert first["readable"] is False

    entry = next(
        row for row in store.rows("feature_store")
        if "user_age_7d" in str(row.get("特征列表", ""))
    )
    assert entry["可用分区"] == ""
    assert entry["存储写入状态"] == "整批扣住未写入"


def test_threshold_change_does_not_rewrite_history(client):
    first = submit(client)["release"]
    assert first["结论"] == "部分放行"

    # 放宽阈值并发布新版本，再提交同一批：新记录变通过，旧拒绝记录保持原样。
    client.post(
        "/api/quality-release/rules",
        json={"rule_no": "QUAL-0002", "values": {"空值阈值": "50%"}},
    )
    second = submit(client, rule_version=None)["release"]
    assert second["规则版本"] == 2
    # 放宽空值阈值后，超阈分区转为可读；空分区没有样本，仍然不能放行。
    assert second["结论"] == "部分放行"
    assert second["可读分区"] == "dt=2026-11-01;dt=2026-11-03"
    assert second["阻断分区"] == "dt=2026-11-02"
    assert second["替代放行编号"] == first["放行编号"]

    old = client.get(f"/api/quality-release/releases/{first['放行编号']}").json()
    assert old["结论"] == "部分放行"
    assert old["status"] == "已替代"
    assert old["规则快照"]["null_threshold_text"] == "5.00%"
    old_blocked = {
        item["partition"]: item["readable"]
        for item in old["分区结果"]
    }
    assert old_blocked["dt=2026-11-03"] is False
    assert old_blocked["dt=2026-11-02"] is False

    recheck = client.get(
        f"/api/quality-release/releases/{first['放行编号']}/recheck"
    ).json()
    assert recheck["frozen"]["matches_record"] is True
    assert recheck["frozen"]["recomputed_conclusion"] == "部分放行"
    # 新规则只是提示，明确标出「若按新阈值会翻转」的分区，不落库。
    assert recheck["current_rule"]["advisory_only"] is True
    assert "dt=2026-11-03" in recheck["would_flip_partitions"]
    still_old = client.get(f"/api/quality-release/releases/{first['放行编号']}").json()
    assert still_old["结论"] == "部分放行"


def test_resubmitting_same_batch_supersedes_not_overwrites(client):
    first = submit(client)["release"]
    second = submit(client)["release"]
    assert second["替代放行编号"] == first["放行编号"]
    old = client.get(f"/api/quality-release/releases/{first['放行编号']}").json()
    assert old["status"] == "已替代"
    # 旧记录的分区和拒绝证据保持冻结，不会被新提交改写。
    assert old["可读分区"] == "dt=2026-11-01"
    assert old["规则版本"] == 1
    listed = client.get("/api/quality-release/releases").json()["items"]
    assert sum(1 for row in listed if row["status"] != "已替代") == 1


def test_multiple_active_rules_require_explicit_selection(client):
    # 再加一份不同规则编号的生效规则后，不指定规则编号的提交必须被拒绝。
    client.post(
        "/api/quality-release/rules",
        json={
            "rule_no": "QUAL-9999",
            "values": {
                "目标特征": "user_gender",
                "检查字段": "gender",
                "空值阈值": "5%",
                "异常值阈值": "1%",
            },
        },
    )
    response = client.post(
        "/api/quality-release/releases",
        json={
            "batch_no": "BATCH-AMBIGUOUS",
            "partitions": [{"partition": "dt=1", "denominator": 10}],
        },
    )
    assert response.status_code == 400
    assert "一份适用规则" in response.json()["detail"]

    # 指定规则后可以正常提交。
    explicit = submit(client, rule_no="QUAL-9999", rule_version=1)
    assert explicit["release"]["规则编号"] == "QUAL-9999"


def test_outlier_threshold_rejects_partition(client):
    release = submit(
        client,
        partitions=[
            {"partition": "dt=2026-11-04", "denominator": 100, "null_count": 0, "outlier_count": 2},
        ],
    )["release"]
    assert release["结论"] == "整批扣住"
    part = release["分区结果"][0]
    assert part["triggered"] == ["异常值超阈"]
    assert part["outlier_ratio_text"] == "2.00%"
    assert part["readable"] is False


def test_failed_submit_does_not_supersede_existing_record(client):
    first = submit(client)["release"]
    response = client.post(
        "/api/quality-release/releases",
        json={
            "rule_no": "QUAL-0002",
            "rule_version": 1,
            "batch_no": "BATCH-20261103",
            "partitions": [{"partition": "dt=1", "denominator": 0, "null_count": 3}],
        },
    )
    assert response.status_code == 400
    old = client.get(f"/api/quality-release/releases/{first['放行编号']}").json()
    assert old["status"] == "部分放行"
    assert len(client.get("/api/quality-release/releases").json()["items"]) == 1


def test_submit_requires_a_single_scope(client):
    # 范围外的分区不参与放行，也不会被当作通过。
    body = submit(client, scope=["dt=2026-11-01", "dt=2026-11-03"])["release"]
    assert body["可读分区"] == "dt=2026-11-01"
    out = next(p for p in body["分区结果"] if p["partition"] == "dt=2026-11-02")
    assert out["verdict"] == "范围外"
    assert out["readable"] is False


def test_empty_partition_is_never_counted_in_denominator(client):
    release = submit(client)["release"]
    empty = next(p for p in release["分区结果"] if p["partition"] == "dt=2026-11-02")
    assert empty["denominator"] == 0
    assert empty["null_ratio"] is None
    assert empty["verdict"] == "拒绝"


def test_unknown_scope_partition_is_400(client):
    response = client.post(
        "/api/quality-release/releases",
        json={
            "rule_no": "QUAL-0002",
            "rule_version": 1,
            "batch_no": "BATCH-X",
            "partitions": [{"partition": "dt=1", "denominator": 10}],
            "scope": ["dt=1", "dt=9"],
        },
    )
    assert response.status_code == 400


def test_field_mismatch_rejects_submit(client):
    response = client.post(
        "/api/quality-release/releases",
        json={
            "rule_no": "QUAL-0002",
            "rule_version": 1,
            "batch_no": "BATCH-X",
            "partitions": [
                {"partition": "dt=1", "denominator": 10, "field": "other_field"}
            ],
        },
    )
    assert response.status_code == 400
    assert "字段" in response.json()["detail"]


def test_preview_does_not_persist(client):
    response = client.post(
        "/api/quality-release/releases/preview",
        json={
            "rule_no": "QUAL-0002",
            "rule_version": 1,
            "batch_no": "BATCH-PREVIEW",
            "partitions": ACCEPTANCE_PARTITIONS,
        },
    )
    assert response.status_code == 200
    assert response.json()["batch"]["conclusion"] == "部分放行"
    assert store.rows("quality_release") == []


def test_alert_level_keeps_partition_readable(client):
    client.post(
        "/api/quality-release/rules",
        json={"rule_no": "QUAL-0002", "values": {"阻断级别": "告警"}},
    )
    release = submit(client, rule_version=None)["release"]
    over = next(p for p in release["分区结果"] if p["partition"] == "dt=2026-11-03")
    assert over["verdict"] == "告警放行"
    assert over["readable"] is True
    # 空分区仍按分区阻断，不会因「告警」级别而被当作通过。
    empty = next(p for p in release["分区结果"] if p["partition"] == "dt=2026-11-02")
    assert empty["verdict"] == "拒绝"
    assert empty["readable"] is False
