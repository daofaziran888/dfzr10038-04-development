"""质量放行领域算法：分区级空值/异常值校验、批次放行结论与拒绝证据。

这里不读写数据仓库，只接收冻结的规则快照和分区抽样结果，返回可序列化的判定。
阈值比较使用 Decimal，保证「恰好等于阈值」按通过处理，不受二进制浮点误差影响。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

# 分区判定
PASS = "通过"
WARN = "告警放行"
REJECT = "拒绝"

# 阻断级别（从轻到重）
LEVEL_ALERT = "告警"
LEVEL_PARTITION = "阻断分区"
LEVEL_BATCH = "阻断整批"
LEVELS: tuple[str, ...] = (LEVEL_ALERT, LEVEL_PARTITION, LEVEL_BATCH)
_LEVEL_RANK = {LEVEL_ALERT: 1, LEVEL_PARTITION: 2, LEVEL_BATCH: 3}

# 批次结论
CONCLUSION_ALL = "全部放行"
CONCLUSION_PARTIAL = "部分放行"
CONCLUSION_HELD = "整批扣住"

# 触发原因
TRIGGER_NULL = "空值超阈"
TRIGGER_OUTLIER = "异常值超阈"
TRIGGER_EMPTY = "无样本"
TRIGGER_OUT_OF_SCOPE = "未纳入放行范围"

OUT_OF_SCOPE = "范围外"
NO_BLOCK = "无阻断"


class ReleaseError(ValueError):
    """质量放行输入或配置不合法。路由层据此返回 400，而不是静默放行。"""


def parse_ratio(value: Any, *, field_name: str = "阈值") -> Decimal:
    """把 5% / 5 / 0.05 / 0.05% 统一解析成 0~1 的小数比例。"""
    if isinstance(value, bool) or value is None:
        raise ReleaseError(f"{field_name}必须是数值或百分比字符串：{value!r}")
    if isinstance(value, Decimal):
        number = value
        percent = False
    elif isinstance(value, (int, float)):
        number = Decimal(str(value))
        percent = False
    else:
        text = str(value).strip()
        if not text:
            raise ReleaseError(f"{field_name}不能为空")
        percent = text.endswith("%")
        if percent:
            text = text[:-1].strip()
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ReleaseError(f"{field_name}无法解析为比例：{value!r}") from exc
    if number < 0:
        raise ReleaseError(f"{field_name}不能为负数：{value!r}")
    ratio = number / Decimal(100) if percent else number
    # 无百分号的 5 视作 5%，避免把 5 当成 500%。
    if not percent and ratio > 1:
        ratio = ratio / Decimal(100)
    if ratio > 1:
        raise ReleaseError(f"{field_name}不能大于 100%：{value!r}")
    return ratio


def ratio_text(ratio: Decimal | None) -> str:
    if ratio is None:
        return "—"
    return f"{(ratio * 100).quantize(Decimal('0.01'))}%"


@dataclass(frozen=True)
class RuleSnapshot:
    """一次提交所确认的唯一适用规则，阈值和版本在此冻结。"""

    rule_no: str
    version: int
    feature: str
    field_name: str
    null_threshold: Decimal
    outlier_threshold: Decimal
    block_level: str = LEVEL_PARTITION
    sample_ratio: str = ""

    def __post_init__(self) -> None:
        if self.block_level not in LEVELS:
            raise ReleaseError(f"未知阻断级别：{self.block_level!r}")
        if not self.rule_no or not self.feature or not self.field_name:
            raise ReleaseError("适用规则、目标特征和检查字段都不能为空")
        if self.version < 1:
            raise ReleaseError("规则版本必须从 1 开始")
        if self.null_threshold < 0 or self.outlier_threshold < 0:
            raise ReleaseError("空值/异常值阈值不能为负数")


@dataclass(frozen=True)
class PartitionCheck:
    """待入库批次里一个分区的抽样结果。denominator 是抽样分母，不是全量行数。"""

    partition: str
    denominator: int
    null_count: int = 0
    outlier_count: int = 0
    field_name: str = ""

    def __post_init__(self) -> None:
        if not str(self.partition or "").strip():
            raise ReleaseError("分区名不能为空")
        if self.denominator < 0:
            raise ReleaseError(f"分区 {self.partition} 的抽样分母不能为负数")
        if self.null_count < 0 or self.outlier_count < 0:
            raise ReleaseError(f"分区 {self.partition} 的空值/异常值数量不能为负数")
        if self.denominator == 0 and (self.null_count or self.outlier_count):
            raise ReleaseError(f"分区 {self.partition} 抽样分母为 0，不应有实测空值/异常值")
        if self.denominator and self.null_count > self.denominator:
            raise ReleaseError(f"分区 {self.partition} 的空值数大于抽样分母")
        if self.denominator and self.outlier_count > self.denominator:
            raise ReleaseError(f"分区 {self.partition} 的异常值数大于抽样分母")

    @property
    def effective_field(self) -> str:
        return self.field_name or ""


@dataclass(frozen=True)
class PartitionResult:
    partition: str
    field_name: str
    denominator: int
    null_count: int
    outlier_count: int
    null_ratio: Decimal | None
    outlier_ratio: Decimal | None
    null_threshold: Decimal
    outlier_threshold: Decimal
    block_level: str
    verdict: str
    readable: bool
    triggered: tuple[str, ...]
    reason: str
    evidence: dict[str, Any]
    local_readable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition": self.partition,
            "field": self.field_name,
            "denominator": self.denominator,
            "null_count": self.null_count,
            "null_ratio": float(self.null_ratio) if self.null_ratio is not None else None,
            "null_ratio_text": ratio_text(self.null_ratio),
            "outlier_count": self.outlier_count,
            "outlier_ratio": float(self.outlier_ratio) if self.outlier_ratio is not None else None,
            "outlier_ratio_text": ratio_text(self.outlier_ratio),
            "null_threshold": float(self.null_threshold),
            "null_threshold_text": ratio_text(self.null_threshold),
            "outlier_threshold": float(self.outlier_threshold),
            "outlier_threshold_text": ratio_text(self.outlier_threshold),
            "block_level": self.block_level,
            "verdict": self.verdict,
            "readable": self.readable,
            "local_readable": self.local_readable,
            "triggered": list(self.triggered),
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "rule_no": self.evidence.get("rule_no", ""),
            "rule_version": self.evidence.get("rule_version", 0),
        }


@dataclass(frozen=True)
class BatchResult:
    batch_no: str
    rule_no: str
    rule_version: int
    feature: str
    field_name: str
    block_level: str
    scope: tuple[str, ...]
    conclusion: str
    partitions: tuple[PartitionResult, ...]
    readable_partitions: tuple[str, ...]
    blocked_partitions: tuple[str, ...]
    readable_rows: int
    total_rows: int
    coverage_ratio: Decimal
    hold_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_no": self.batch_no,
            "rule_no": self.rule_no,
            "rule_version": self.rule_version,
            "feature": self.feature,
            "field": self.field_name,
            "block_level": self.block_level,
            "scope": list(self.scope),
            "conclusion": self.conclusion,
            "batch_held": self.conclusion == CONCLUSION_HELD,
            "has_readable_partitions": bool(self.readable_partitions),
            "partitions": [item.to_dict() for item in self.partitions],
            "readable_partitions": list(self.readable_partitions),
            "blocked_partitions": list(self.blocked_partitions),
            "readable_rows": self.readable_rows,
            "total_rows": self.total_rows,
            "coverage_ratio": float(self.coverage_ratio),
            "coverage_ratio_text": ratio_text(self.coverage_ratio),
            "hold_reason": self.hold_reason,
            "rejection_evidence": [
                item.to_dict() for item in self.partitions if item.verdict == REJECT
            ],
        }


def _effective_block_level(snapshot: RuleSnapshot, empty: bool) -> str:
    """空分区必须按分区阻断处理，哪怕规则只配置了告警。"""
    if not empty:
        return snapshot.block_level
    if _LEVEL_RANK[snapshot.block_level] >= _LEVEL_RANK[LEVEL_PARTITION]:
        return snapshot.block_level
    return LEVEL_PARTITION


def _evidence(
    snapshot: RuleSnapshot,
    check: PartitionCheck,
    field_name: str,
    null_ratio: Decimal | None,
    outlier_ratio: Decimal | None,
    level: str,
    verdict: str,
    reason: str,
    triggered: Sequence[str],
) -> dict[str, Any]:
    return {
        "partition": check.partition,
        "field": field_name,
        "rule_no": snapshot.rule_no,
        "rule_version": snapshot.version,
        "denominator": check.denominator,
        "null_count": check.null_count,
        "null_ratio": ratio_text(null_ratio),
        "null_threshold": ratio_text(snapshot.null_threshold),
        "outlier_count": check.outlier_count,
        "outlier_ratio": ratio_text(outlier_ratio),
        "outlier_threshold": ratio_text(snapshot.outlier_threshold),
        "block_level": level,
        "verdict": verdict,
        "triggered": list(triggered),
        "reason": reason,
    }


def evaluate_partition(snapshot: RuleSnapshot, check: PartitionCheck) -> PartitionResult:
    """按冻结规则校验单个分区；分区级可读性在这里判定。"""
    field_name = check.field_name or snapshot.field_name
    if check.denominator == 0:
        level = _effective_block_level(snapshot, empty=True)
        reason = (
            f"分区 {check.partition} 的字段 {field_name} 无样本（抽样分母 0），"
            "无法证明覆盖率，不能视为通过"
        )
        return PartitionResult(
            partition=check.partition,
            field_name=field_name,
            denominator=0,
            null_count=check.null_count,
            outlier_count=check.outlier_count,
            null_ratio=None,
            outlier_ratio=None,
            null_threshold=snapshot.null_threshold,
            outlier_threshold=snapshot.outlier_threshold,
            block_level=level,
            verdict=REJECT,
            readable=False,
            triggered=(TRIGGER_EMPTY,),
            reason=reason,
            evidence=_evidence(
                snapshot, check, field_name, None, None, level, REJECT, reason,
                (TRIGGER_EMPTY,),
            ),
            local_readable=False,
        )

    denominator = Decimal(check.denominator)
    null_ratio = Decimal(check.null_count) / denominator
    outlier_ratio = Decimal(check.outlier_count) / denominator
    null_breach = null_ratio > snapshot.null_threshold
    outlier_breach = outlier_ratio > snapshot.outlier_threshold
    triggered = tuple(
        name for name, hit in ((TRIGGER_NULL, null_breach), (TRIGGER_OUTLIER, outlier_breach)) if hit
    )

    if not triggered:
        reason = (
            f"分区 {check.partition} 的字段 {field_name} 空值比例 "
            f"{ratio_text(null_ratio)} 未超过阈值 {ratio_text(snapshot.null_threshold)}，"
            f"异常值比例 {ratio_text(outlier_ratio)} 未超过阈值 "
            f"{ratio_text(snapshot.outlier_threshold)}，通过"
        )
        return PartitionResult(
            partition=check.partition,
            field_name=field_name,
            denominator=check.denominator,
            null_count=check.null_count,
            outlier_count=check.outlier_count,
            null_ratio=null_ratio,
            outlier_ratio=outlier_ratio,
            null_threshold=snapshot.null_threshold,
            outlier_threshold=snapshot.outlier_threshold,
            block_level=NO_BLOCK,
            verdict=PASS,
            readable=True,
            triggered=(),
            reason=reason,
            evidence=_evidence(
                snapshot, check, field_name, null_ratio, outlier_ratio, "", PASS, reason, (),
            ),
            local_readable=True,
        )

    level = _effective_block_level(snapshot, empty=False)
    parts: list[str] = []
    if null_breach:
        parts.append(
            f"空值比例 {ratio_text(null_ratio)} 高于阈值 {ratio_text(snapshot.null_threshold)}"
        )
    if outlier_breach:
        parts.append(
            f"异常值比例 {ratio_text(outlier_ratio)} 高于阈值 "
            f"{ratio_text(snapshot.outlier_threshold)}"
        )
    if level == LEVEL_ALERT:
        verdict = WARN
        readable = True
        reason = f"分区 {check.partition} 的字段 {field_name} " + "；".join(parts) + "，按告警放行"
    else:
        verdict = REJECT
        readable = False
        reason = (
            f"分区 {check.partition} 的字段 {field_name} " + "；".join(parts) + f"，触发{level}"
        )
    return PartitionResult(
        partition=check.partition,
        field_name=field_name,
        denominator=check.denominator,
        null_count=check.null_count,
        outlier_count=check.outlier_count,
        null_ratio=null_ratio,
        outlier_ratio=outlier_ratio,
        null_threshold=snapshot.null_threshold,
        outlier_threshold=snapshot.outlier_threshold,
        block_level=level,
        verdict=verdict,
        readable=readable,
        triggered=triggered,
        reason=reason,
        evidence=_evidence(
            snapshot, check, field_name, null_ratio, outlier_ratio, level, verdict, reason, triggered,
        ),
        local_readable=readable,
    )


def evaluate_batch(
    snapshot: RuleSnapshot,
    batch_no: str,
    checks: Iterable[PartitionCheck],
    *,
    scope: Sequence[str] | None = None,
) -> BatchResult:
    """校验一个入库批的全部分区，并给出「整批扣住 / 可读通过分区」的结论。"""
    if not str(batch_no or "").strip():
        raise ReleaseError("入库批次不能为空")
    check_list = list(checks)
    if not check_list:
        raise ReleaseError("放行范围不能为空，至少需要一个分区")
    names = [check.partition for check in check_list]
    if len(set(names)) != len(names):
        raise ReleaseError("同一入库批内分区名不能重复")
    scope_list = list(scope) if scope else names
    if not scope_list:
        raise ReleaseError("放行范围不能为空")
    unknown = sorted(set(scope_list) - set(names))
    if unknown:
        raise ReleaseError(f"放行范围包含未提交的分区：{'、'.join(unknown)}")

    evaluated: list[PartitionResult] = []
    for check in check_list:
        if check.partition not in scope_list:
            evaluated.append(_out_of_scope(snapshot, check))
        else:
            evaluated.append(evaluate_partition(snapshot, check))
    in_scope = [item for item in evaluated if item.partition in set(scope_list)]
    level_hold = any(
        item.block_level == LEVEL_BATCH and not item.local_readable for item in in_scope
    )
    locally_readable = [item for item in in_scope if item.local_readable]
    outside = len(evaluated) - len(in_scope)

    if level_hold:
        hold_reason = (
            "存在分区触发阻断整批："
            + "、".join(
                item.partition
                for item in in_scope
                if item.block_level == LEVEL_BATCH and not item.local_readable
            )
            + "；整批不可读，通过分区也不单独放行"
        )
        final = [
            _force_unreadable(item, hold_reason) for item in evaluated
        ]
        conclusion = CONCLUSION_HELD
    elif not locally_readable:
        hold_reason = "放行范围内没有可读分区，整批不可读"
        final = [_force_unreadable(item, hold_reason) for item in evaluated]
        conclusion = CONCLUSION_HELD
    elif len(locally_readable) == len(evaluated):
        hold_reason = ""
        final = evaluated
        conclusion = CONCLUSION_ALL
    else:
        hold_reason = ""
        final = evaluated
        conclusion = CONCLUSION_PARTIAL

    readable = [item for item in final if item.readable]
    blocked = [item for item in final if not item.readable]
    total_rows = sum(item.denominator for item in in_scope)
    readable_rows = sum(
        item.denominator for item in readable if item.partition in set(scope_list)
    )
    coverage = (
        Decimal(readable_rows) / Decimal(total_rows) if total_rows else Decimal(0)
    )
    return BatchResult(
        batch_no=batch_no,
        rule_no=snapshot.rule_no,
        rule_version=snapshot.version,
        feature=snapshot.feature,
        field_name=snapshot.field_name,
        block_level=snapshot.block_level,
        scope=tuple(scope_list),
        conclusion=conclusion,
        partitions=tuple(final),
        readable_partitions=tuple(item.partition for item in readable),
        blocked_partitions=tuple(item.partition for item in blocked),
        readable_rows=readable_rows,
        total_rows=total_rows,
        coverage_ratio=coverage,
        hold_reason=hold_reason,
    )


def _out_of_scope(snapshot: RuleSnapshot, check: PartitionCheck) -> PartitionResult:
    """提交了但不在本次放行范围内的分区：只记录分母，不参与放行结论。"""
    field_name = check.field_name or snapshot.field_name
    reason = (
        f"分区 {check.partition} 未纳入本次放行范围（范围外），"
        "不参与可读结论，也不得据此刻度放行"
    )
    return PartitionResult(
        partition=check.partition,
        field_name=field_name,
        denominator=check.denominator,
        null_count=check.null_count,
        outlier_count=check.outlier_count,
        null_ratio=None,
        outlier_ratio=None,
        null_threshold=snapshot.null_threshold,
        outlier_threshold=snapshot.outlier_threshold,
        block_level=OUT_OF_SCOPE,
        verdict=OUT_OF_SCOPE,
        readable=False,
        triggered=(TRIGGER_OUT_OF_SCOPE,),
        reason=reason,
        evidence={
            "partition": check.partition,
            "field": field_name,
            "rule_no": snapshot.rule_no,
            "rule_version": snapshot.version,
            "denominator": check.denominator,
            "block_level": OUT_OF_SCOPE,
            "verdict": OUT_OF_SCOPE,
            "triggered": [TRIGGER_OUT_OF_SCOPE],
            "reason": reason,
        },
        local_readable=False,
    )


def _force_unreadable(item: PartitionResult, hold_reason: str) -> PartitionResult:
    """整批扣住时，把本地通过分区的可读性收回，同时保留其原始判定。"""
    if not item.local_readable:
        return item
    reason = f"分区本身通过，但{hold_reason}"
    return PartitionResult(
        partition=item.partition,
        field_name=item.field_name,
        denominator=item.denominator,
        null_count=item.null_count,
        outlier_count=item.outlier_count,
        null_ratio=item.null_ratio,
        outlier_ratio=item.outlier_ratio,
        null_threshold=item.null_threshold,
        outlier_threshold=item.outlier_threshold,
        block_level=item.block_level,
        verdict=item.verdict,
        readable=False,
        triggered=item.triggered,
        reason=reason,
        evidence={**item.evidence, "readable": False, "reason": reason},
        local_readable=True,
    )
