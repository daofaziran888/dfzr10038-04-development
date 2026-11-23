"""质量放行领域算法单测：只测纯函数，不碰数据仓库。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.quality_release import (
    CONCLUSION_ALL,
    CONCLUSION_HELD,
    CONCLUSION_PARTIAL,
    LEVEL_ALERT,
    LEVEL_BATCH,
    LEVEL_PARTITION,
    PASS,
    REJECT,
    WARN,
    PartitionCheck,
    ReleaseError,
    RuleSnapshot,
    evaluate_batch,
    evaluate_partition,
    parse_ratio,
)


def snapshot(**overrides) -> RuleSnapshot:
    values = dict(
        rule_no="QUAL-0002",
        version=1,
        feature="user_age_7d",
        field_name="age",
        null_threshold=Decimal("0.05"),
        outlier_threshold=Decimal("0.01"),
        block_level=LEVEL_PARTITION,
    )
    values.update(overrides)
    return RuleSnapshot(**values)


def test_parse_ratio_accepts_percent_number_and_decimal():
    assert parse_ratio("5%") == Decimal("0.05")
    assert parse_ratio("5") == Decimal("0.05")
    assert parse_ratio(0.05) == Decimal("0.05")
    assert parse_ratio("0.05") == Decimal("0.05")
    assert parse_ratio("100%") == Decimal("1")


@pytest.mark.parametrize("bad", ["", "abc", None, True, -1, "120%"])
def test_parse_ratio_rejects_bad_values(bad):
    with pytest.raises(ReleaseError):
        parse_ratio(bad)


def test_equal_to_threshold_passes_exactly():
    # 100 个样本里 5 个空值 = 5%，阈值 5%，恰好等于阈值不算超阈。
    result = evaluate_partition(
        snapshot(), PartitionCheck(partition="dt=2026-11-01", denominator=100, null_count=5)
    )
    assert result.null_ratio == Decimal("0.05")
    assert result.verdict == PASS
    assert result.readable is True
    assert result.triggered == ()


def test_one_over_threshold_rejects_with_partition_evidence():
    result = evaluate_partition(
        snapshot(), PartitionCheck(partition="dt=2026-11-02", denominator=100, null_count=6)
    )
    assert result.verdict == REJECT
    assert result.readable is False
    assert result.triggered == ("空值超阈",)
    assert result.evidence["partition"] == "dt=2026-11-02"
    assert result.evidence["field"] == "age"
    assert result.evidence["null_ratio"] == "6.00%"


def test_empty_partition_is_never_a_pass():
    result = evaluate_partition(
        snapshot(), PartitionCheck(partition="dt=2026-11-03", denominator=0)
    )
    assert result.verdict == REJECT
    assert result.readable is False
    assert result.triggered == ("无样本",)
    assert result.null_ratio is None
    assert "无样本" in result.reason


def test_empty_partition_escalates_alert_level_to_partition_block():
    result = evaluate_partition(
        snapshot(block_level=LEVEL_ALERT),
        PartitionCheck(partition="dt=2026-11-03", denominator=0),
    )
    assert result.block_level == LEVEL_PARTITION
    assert result.verdict == REJECT


def test_alert_level_still_readable_when_over_threshold():
    result = evaluate_partition(
        snapshot(block_level=LEVEL_ALERT),
        PartitionCheck(partition="dt=2026-11-02", denominator=10, null_count=3),
    )
    assert result.verdict == WARN
    assert result.readable is True


def test_batch_with_last_partition_over_threshold_is_partial():
    result = evaluate_batch(
        snapshot(),
        "BATCH-20261102",
        [
            PartitionCheck("dt=2026-11-01", 100, null_count=2),
            PartitionCheck("dt=2026-11-02", 100, null_count=3),
            PartitionCheck("dt=2026-11-03", 100, null_count=6),
        ],
    )
    assert result.conclusion == CONCLUSION_PARTIAL
    assert result.readable_partitions == ("dt=2026-11-01", "dt=2026-11-02")
    assert result.blocked_partitions == ("dt=2026-11-03",)
    assert result.readable_rows == 200
    assert result.total_rows == 300


def test_batch_block_level_holds_the_whole_batch():
    result = evaluate_batch(
        snapshot(block_level=LEVEL_BATCH),
        "BATCH-20261102",
        [
            PartitionCheck("dt=2026-11-01", 100, null_count=2),
            PartitionCheck("dt=2026-11-03", 100, null_count=6),
        ],
    )
    assert result.conclusion == CONCLUSION_HELD
    assert result.readable_partitions == ()
    assert result.readable_rows == 0
    # 本区通过的分区仍保留本地判定，但对外不可读。
    first = result.partitions[0]
    assert first.local_readable is True
    assert first.readable is False


def test_all_partitions_pass():
    result = evaluate_batch(
        snapshot(),
        "BATCH-20261101",
        [
            PartitionCheck("dt=2026-11-01", 100, null_count=2),
            PartitionCheck("dt=2026-11-02", 100, null_count=5),
        ],
    )
    assert result.conclusion == CONCLUSION_ALL
    assert result.readable_rows == 200


def test_scope_limits_release_and_out_of_scope_is_not_readable():
    result = evaluate_batch(
        snapshot(),
        "BATCH-20261102",
        [
            PartitionCheck("dt=2026-11-01", 100, null_count=2),
            PartitionCheck("dt=2026-11-03", 100, null_count=6),
        ],
        scope=["dt=2026-11-01"],
    )
    assert result.conclusion == CONCLUSION_PARTIAL
    assert result.readable_partitions == ("dt=2026-11-01",)
    out = [p for p in result.partitions if p.partition == "dt=2026-11-03"][0]
    assert out.verdict == "范围外"
    assert out.readable is False


def test_scope_with_unknown_partition_is_rejected():
    with pytest.raises(ReleaseError):
        evaluate_batch(
            snapshot(),
            "BATCH-1",
            [PartitionCheck("dt=1", 10)],
            scope=["dt=1", "dt=2"],
        )


def test_duplicate_partitions_are_rejected():
    with pytest.raises(ReleaseError):
        evaluate_batch(
            snapshot(),
            "BATCH-1",
            [PartitionCheck("dt=1", 10), PartitionCheck("dt=1", 10)],
        )


def test_denominator_zero_with_nonzero_null_is_rejected_input():
    with pytest.raises(ReleaseError):
        PartitionCheck("dt=1", 0, null_count=1)


def test_batch_requires_at_least_one_partition():
    with pytest.raises(ReleaseError):
        evaluate_batch(snapshot(), "BATCH-1", [])
