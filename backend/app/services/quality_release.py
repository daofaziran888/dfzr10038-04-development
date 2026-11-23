"""质量放行服务：把分区抽样校验结果固化成不可回改的放行记录。

职责边界：
- 规则版本只追加，不覆盖；每次提交冻结「一份规则版本 + 一个放行范围」。
- 放行记录一经提交即不可变，后续阈值调整只能生成新版本、新记录。
- 分区级放行结果同步成存储清单，并回写特征存储的可用分区。
"""
from __future__ import annotations

import copy
import datetime
from typing import Any, Iterable

from app.domain.quality_release import (
    LEVEL_ALERT,
    LEVEL_BATCH,
    LEVEL_PARTITION,
    LEVELS,
    BatchResult,
    PartitionCheck,
    ReleaseError,
    RuleSnapshot,
    evaluate_batch,
    parse_ratio,
    ratio_text,
)
from app.store import store

RULES_MODULE = "quality_rule_version"
RELEASES_MODULE = "quality_release"

# 放行记录状态
ALL_RELEASED = "全部放行"
PARTIAL_RELEASED = "部分放行"
BATCH_HELD = "整批扣住"
SUPERSEDED = "已替代"

# 存储写入状态
STORE_WRITTEN = "写入通过分区"
STORE_HELD = "整批扣住未写入"


class ReleaseNotFound(LookupError):
    pass


# ---------------------------------------------------------------------------
# 规则版本
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _rule_rows() -> list[dict[str, Any]]:
    return store.rows(RULES_MODULE)


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def list_rules() -> list[dict[str, Any]]:
    return [copy.deepcopy(row) for row in _rule_rows()]


def _find_rule(rule_no: str | None, version: int | None) -> dict[str, Any]:
    """取唯一一份适用规则；没有或有多份都算配置冲突。"""
    candidates = [
        row for row in _rule_rows()
        if (rule_no is None or str(row.get("规则编号")) == rule_no)
        and (version is None or int(row.get("版本", 0)) == version)
        and row.get("状态") != SUPERSEDED
    ]
    if not candidates:
        raise ReleaseError(f"没有找到适用规则：{rule_no or '任意'} / 版本 {version or '最新'}")
    if len(candidates) > 1:
        raise ReleaseError(
            "一次提交只能确认一份适用规则，当前命中多份，请在请求里指定规则编号和版本"
        )
    row = candidates[0]
    if rule_no is None:
        # 不指定规则时必须无歧义：只允许存在唯一一份生效规则。
        active = [item for item in _rule_rows() if item.get("状态") == "生效中"]
        if len(active) > 1:
            raise ReleaseError("存在多份生效规则，提交时必须指定唯一适用规则")
    return row


def snapshot_from_rule(row: dict[str, Any]) -> RuleSnapshot:
    return RuleSnapshot(
        rule_no=str(row.get("规则编号", "")),
        version=int(row.get("版本", 0)),
        feature=str(row.get("目标特征", "")),
        field_name=str(row.get("检查字段") or row.get("目标特征") or ""),
        null_threshold=parse_ratio(row.get("空值阈值"), field_name="空值阈值"),
        outlier_threshold=parse_ratio(row.get("异常值阈值"), field_name="异常值阈值"),
        block_level=str(row.get("阻断级别") or LEVEL_PARTITION),
        sample_ratio=str(row.get("采样比例", "")),
    )


def publish_rule_version(rule_no: str, values: dict[str, Any]) -> dict[str, Any]:
    """追加一份新规则版本；旧版本标记已替代，已有放行记录的冻结阈值不变。"""
    rule_no = str(rule_no or "").strip()
    if not rule_no:
        raise ReleaseError("规则编号不能为空")
    rows = _rule_rows()
    same = [row for row in rows if str(row.get("规则编号")) == rule_no]
    base = copy.deepcopy(same[-1]) if same else {}
    merged = {**base, **{key: value for key, value in values.items() if value not in (None, "")}}
    merged["规则编号"] = rule_no
    merged["版本"] = max((int(row.get("版本", 0)) for row in same), default=0) + 1
    # 先校验再落库，避免出现半截的坏版本。
    snapshot_from_rule(merged)
    if snapshot_from_rule(merged).block_level not in LEVELS:
        raise ReleaseError("阻断级别只能是：" + "、".join(LEVELS))
    for row in same:
        row["状态"] = SUPERSEDED
    merged["状态"] = "生效中"
    merged["发布时间"] = _now()
    merged["id"] = _next_id(rows)
    rows.append(merged)
    return copy.deepcopy(merged)


# ---------------------------------------------------------------------------
# 请求解析
# ---------------------------------------------------------------------------


def _int_field(payload: dict[str, Any], *names: str, default: int | None = None) -> int | None:
    for name in names:
        if name in payload and payload[name] is not None and payload[name] != "":
            try:
                return int(payload[name])
            except (TypeError, ValueError) as exc:
                raise ReleaseError(f"字段 {name} 必须是整数：{payload[name]!r}") from exc
    return default


def parse_partitions(raw: Iterable[dict[str, Any]]) -> list[PartitionCheck]:
    checks: list[PartitionCheck] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ReleaseError("分区抽样结果必须是对象数组")
        partition = str(item.get("partition") or item.get("分区") or "").strip()
        denominator = _int_field(item, "denominator", "抽样分母", default=None)
        if denominator is None:
            raise ReleaseError(f"分区 {partition or '?'} 缺少抽样分母 denominator")
        checks.append(
            PartitionCheck(
                partition=partition,
                denominator=denominator,
                null_count=_int_field(item, "null_count", "空值数", default=0) or 0,
                outlier_count=_int_field(item, "outlier_count", "异常值数", default=0) or 0,
                field_name=str(item.get("field") or item.get("字段") or ""),
            )
        )
    return checks


def _parse_scope(raw: Any, checks: list[PartitionCheck]) -> list[str]:
    if raw in (None, "", []):
        return [check.partition for check in checks]
    if isinstance(raw, str):
        scope = [part.strip() for part in raw.replace(",", ";").split(";") if part.strip()]
    elif isinstance(raw, list):
        scope = [str(part).strip() for part in raw if str(part).strip()]
    else:
        raise ReleaseError("放行范围必须是分区名数组或分号分隔字符串")
    if not scope:
        raise ReleaseError("放行范围不能为空")
    if len(set(scope)) != len(scope):
        raise ReleaseError("放行范围内分区名不能重复")
    return scope


# ---------------------------------------------------------------------------
# 记录编号 / 存取
# ---------------------------------------------------------------------------


def _next_release_no(rows: list[dict[str, Any]]) -> str:
    existing = {
        str(row.get("放行编号", "")) for row in rows
    }
    index = 1
    while f"QREL-{index:04d}" in existing:
        index += 1
    return f"QREL-{index:04d}"


def list_releases() -> list[dict[str, Any]]:
    return [_summary(row) for row in store.rows(RELEASES_MODULE)]


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "status", "pending", "abnormal", "放行编号", "入库批次", "规则编号",
        "规则版本", "目标特征", "检查字段", "阻断级别", "放行范围", "可读分区",
        "阻断分区", "结论", "抽样分母合计", "可读行数", "覆盖率", "提交时间",
        "存储写入状态", "可用分区", "替代放行编号",
    )
    return {key: copy.deepcopy(row.get(key)) for key in keys}


def get_release(release_no: str) -> dict[str, Any]:
    for row in store.rows(RELEASES_MODULE):
        if str(row.get("放行编号")) == str(release_no):
            return copy.deepcopy(row)
    raise ReleaseNotFound(f"放行记录 {release_no} 不存在")


def _status_for(result: BatchResult) -> str:
    if result.conclusion == "整批扣住":
        return BATCH_HELD
    if result.conclusion == "全部放行":
        return ALL_RELEASED
    return PARTIAL_RELEASED


def preview_release(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot, batch_no, checks, scope = _prepare(payload)
    result = evaluate_batch(snapshot, batch_no, checks, scope=scope)
    return {
        "preview": True,
        "rule": _snapshot_dict(snapshot),
        "batch": result.to_dict(),
    }


def _prepare(
    payload: dict[str, Any]
) -> tuple[RuleSnapshot, str, list[PartitionCheck], list[str]]:
    rule_no = payload.get("rule_no") or payload.get("规则编号")
    version = _int_field(payload, "rule_version", "规则版本", default=None)
    rule_row = _find_rule(str(rule_no) if rule_no else None, version)
    snapshot = snapshot_from_rule(rule_row)

    batch_no = str(payload.get("batch_no") or payload.get("入库批次") or "").strip()
    if not batch_no:
        raise ReleaseError("入库批次不能为空")
    raw_partitions = payload.get("partitions") or payload.get("分区抽样") or []
    if not isinstance(raw_partitions, list) or not raw_partitions:
        raise ReleaseError("至少提交一个分区的抽样结果")
    checks = parse_partitions(raw_partitions)
    scope = _parse_scope(payload.get("scope") or payload.get("放行范围"), checks)

    mismatch = sorted(
        {
            check.partition for check in checks
            if check.field_name and check.field_name != snapshot.field_name
        }
    )
    if mismatch:
        raise ReleaseError(
            f"分区 {'、'.join(mismatch)} 的检查字段与适用规则字段 "
            f"{snapshot.field_name} 不一致，一次提交只能确认一份适用规则"
        )
    return snapshot, batch_no, checks, scope


def _snapshot_dict(snapshot: RuleSnapshot) -> dict[str, Any]:
    return {
        "rule_no": snapshot.rule_no,
        "version": snapshot.version,
        "feature": snapshot.feature,
        "field": snapshot.field_name,
        "null_threshold": float(snapshot.null_threshold),
        "null_threshold_text": ratio_text(snapshot.null_threshold),
        "outlier_threshold": float(snapshot.outlier_threshold),
        "outlier_threshold_text": ratio_text(snapshot.outlier_threshold),
        "block_level": snapshot.block_level,
        "sample_ratio": snapshot.sample_ratio,
    }


def submit_release(payload: dict[str, Any]) -> dict[str, Any]:
    """提交一次放行：冻结规则版本与范围，落放行记录并同步存储清单。"""
    snapshot, batch_no, checks, scope = _prepare(payload)
    # 先算结论，校验失败时不会动到已有记录。
    result = evaluate_batch(snapshot, batch_no, checks, scope=scope)
    rows = store.rows(RELEASES_MODULE)

    superseded_no = ""
    for row in rows:
        # 同一批次 + 同一规则只保留一份最新放行记录；旧记录原样冻结，只标记已替代。
        if (
            str(row.get("入库批次")) == batch_no
            and str(row.get("规则编号")) == snapshot.rule_no
            and row.get("status") != SUPERSEDED
        ):
            row["status"] = SUPERSEDED
            row["pending"] = False
            superseded_no = str(row.get("放行编号", ""))

    release_no = _next_release_no(rows)
    record = {
        "id": _next_id(rows),
        "status": _status_for(result),
        "pending": result.conclusion != "全部放行",
        "abnormal": result.conclusion == "整批扣住",
        "放行编号": release_no,
        "入库批次": batch_no,
        "规则编号": snapshot.rule_no,
        "规则版本": snapshot.version,
        "目标特征": snapshot.feature,
        "检查字段": snapshot.field_name,
        "阻断级别": snapshot.block_level,
        "放行范围": ";".join(result.scope),
        "可读分区": ";".join(result.readable_partitions),
        "阻断分区": ";".join(result.blocked_partitions),
        "结论": result.conclusion,
        "抽样分母合计": result.total_rows,
        "可读行数": result.readable_rows,
        "覆盖率": ratio_text(result.coverage_ratio),
        "提交时间": _now(),
        "替代放行编号": superseded_no,
        "规则快照": _snapshot_dict(snapshot),
        "分区结果": list(result.to_dict()["partitions"]),
        "拒绝证据": list(result.to_dict()["rejection_evidence"]),
        "扣留原因": result.to_dict()["hold_reason"],
    }
    manifest = _build_manifest(record)
    record["存储清单"] = manifest
    record["存储写入状态"] = manifest["store_write_status"]
    record["可用分区"] = ";".join(manifest["readable_partitions"])
    record["manifest_version"] = manifest["version"]
    _apply_to_feature_store(record, manifest)
    rows.append(record)
    return copy.deepcopy(record)


def _build_manifest(record: dict[str, Any]) -> dict[str, Any]:
    """把分区放行结果整理成离线消费者可读的存储清单。"""
    readable = [part for part in record["分区结果"] if part["readable"]]
    blocked = [part for part in record["分区结果"] if not part["readable"]]
    return {
        "version": 1,
        "registry": "quality_release_manifest",
        "release_no": record["放行编号"],
        "batch_no": record["入库批次"],
        "rule_no": record["规则编号"],
        "rule_version": record["规则版本"],
        "feature": record["目标特征"],
        "field": record["检查字段"],
        "conclusion": record["结论"],
        "batch_held": record["结论"] == "整批扣住",
        "has_readable_partitions": bool(readable),
        "readable_partitions": [part["partition"] for part in readable],
        "blocked_partitions": [part["partition"] for part in blocked],
        "partition_status": [
            {
                "partition": part["partition"],
                "field": part["field"],
                "readable": part["readable"],
                "verdict": part["verdict"],
                "null_ratio": part["null_ratio_text"],
                "null_threshold": part["null_threshold_text"],
                "block_level": part["block_level"],
            }
            for part in record["分区结果"]
        ],
        "store_write_status": (
            STORE_HELD if record["结论"] == "整批扣住" else STORE_WRITTEN
        ),
        "generated_at": record["提交时间"],
    }


def _apply_to_feature_store(record: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """按放行数据写入特征存储的可用分区；整批扣住时一个分区都不写。"""
    stores = store.rows("feature_store")
    target = None
    for row in stores:
        if record["目标特征"] in str(row.get("特征列表", "")):
            target = row
            break
    if target is None:
        for row in stores:
            if row.get("status") == "已启用":
                target = row
                break
    if target is None and stores:
        target = stores[0]
    if target is None:
        return {}

    target["可用分区"] = ";".join(manifest["readable_partitions"])
    target["不可用分区"] = ";".join(manifest["blocked_partitions"])
    target["质量放行编号"] = record["放行编号"]
    target["最近放行时间"] = record["提交时间"]
    target["存储写入状态"] = manifest["store_write_status"]
    if record["结论"] == "整批扣住":
        target["pending"] = True
        target["abnormal"] = True
    else:
        target["pending"] = bool(manifest["blocked_partitions"])
        target["abnormal"] = bool(manifest["blocked_partitions"])
    return target


def get_manifest(release_no: str) -> dict[str, Any]:
    record = get_release(release_no)
    manifest = record.get("存储清单")
    if not manifest:
        raise ReleaseNotFound(f"放行记录 {release_no} 没有存储清单")
    return copy.deepcopy(manifest)


def recheck_release(release_no: str) -> dict[str, Any]:
    """用冻结阈值复核历史记录，并展示当前新规则会给出的结论（仅提示，不改记录）。"""
    record = get_release(release_no)
    frozen = RuleSnapshot(
        rule_no=record["规则编号"],
        version=int(record["规则版本"]),
        feature=record["目标特征"],
        field_name=record["检查字段"],
        null_threshold=parse_ratio(record["规则快照"]["null_threshold_text"]),
        outlier_threshold=parse_ratio(record["规则快照"]["outlier_threshold_text"]),
        block_level=record["规则快照"]["block_level"],
        sample_ratio=record["规则快照"].get("sample_ratio", ""),
    )
    checks = [
        PartitionCheck(
            partition=part["partition"],
            denominator=int(part["denominator"]),
            null_count=int(part["null_count"]),
            outlier_count=int(part["outlier_count"]),
            field_name=part["field"],
        )
        for part in record["分区结果"]
    ]
    scope = [part.strip() for part in str(record["放行范围"]).split(";") if part.strip()]
    frozen_result = evaluate_batch(frozen, record["入库批次"], checks, scope=scope)

    current_rows = [
        row for row in _rule_rows()
        if str(row.get("规则编号")) == record["规则编号"]
    ]
    current = max(current_rows, key=lambda row: int(row.get("版本", 0)), default=None)
    response: dict[str, Any] = {
        "release_no": release_no,
        "frozen": {
            "rule_version": frozen.version,
            "conclusion": record["结论"],
            "readable_partitions": list(record.get("可读分区", "").split(";"))
            if record.get("可读分区") else [],
            "recomputed_conclusion": frozen_result.conclusion,
            "recomputed_readable": list(frozen_result.readable_partitions),
            "matches_record": (
                frozen_result.conclusion == record["结论"]
                and list(frozen_result.readable_partitions)
                == ([p for p in str(record.get("可读分区", "")).split(";") if p])
            ),
        },
        "current_rule": None,
        "silently_flipped": False,
    }
    if current is not None:
        current_snapshot = snapshot_from_rule(current)
        current_result = evaluate_batch(current_snapshot, record["入库批次"], checks, scope=scope)
        response["current_rule"] = {
            "rule_version": current_snapshot.version,
            "conclusion": current_result.conclusion,
            "readable_partitions": list(current_result.readable_partitions),
            "advisory_only": True,
            "note": "当前规则版本仅供参考，不回写历史放行记录",
        }
        previously_blocked = set(frozen_result.blocked_partitions)
        newly_readable = previously_blocked & set(current_result.readable_partitions)
        response["silently_flipped"] = bool(
            current_snapshot.version != frozen.version and newly_readable
        )
        response["would_flip_partitions"] = sorted(newly_readable)
    return response
