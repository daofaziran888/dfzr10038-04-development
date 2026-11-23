/** 质量放行：规则版本、分区校验与交付存储的数据契约。 */

export type RuleVersion = {
  id: number
  规则编号: string
  版本: number
  目标特征: string
  检查字段: string
  检查类型?: string
  空值阈值: string
  异常值阈值: string
  阻断级别: string
  采样比例?: string
  状态: string
  发布时间?: string
}

export type PartitionInput = {
  partition: string
  denominator: number
  null_count: number
  outlier_count: number
}

export type PartitionOutcome = {
  partition: string
  field: string
  denominator: number
  null_count: number
  null_ratio: number | null
  null_ratio_text: string
  outlier_count: number
  outlier_ratio: number | null
  outlier_ratio_text: string
  null_threshold: number
  null_threshold_text: string
  outlier_threshold: number
  outlier_threshold_text: string
  block_level: string
  verdict: string
  readable: boolean
  local_readable: boolean
  triggered: string[]
  reason: string
}

export type BatchOutcome = {
  batch_no: string
  rule_no: string
  rule_version: number
  feature: string
  field: string
  block_level: string
  scope: string[]
  conclusion: string
  partitions: PartitionOutcome[]
  readable_partitions: string[]
  blocked_partitions: string[]
  readable_rows: number
  total_rows: number
  coverage_ratio: number
  coverage_ratio_text: string
  hold_reason: string
  rejection_evidence: PartitionOutcome[]
}

export type ReleaseRecord = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  放行编号: string
  入库批次: string
  规则编号: string
  规则版本: number
  目标特征: string
  检查字段: string
  阻断级别: string
  放行范围: string
  可读分区: string
  阻断分区: string
  结论: string
  抽样分母合计: number
  可读行数: number
  覆盖率: string
  提交时间: string
  存储写入状态: string
  可用分区: string
  替代放行编号?: string
}

export type ReleaseDetail = ReleaseRecord & {
  规则快照: Record<string, unknown>
  分区结果: PartitionOutcome[]
  拒绝证据: PartitionOutcome[]
  扣留原因: string
  存储清单: StorageManifest
}

export type StorageManifest = {
  release_no: string
  batch_no: string
  rule_no: string
  rule_version: number
  feature: string
  field: string
  conclusion: string
  readable_partitions: string[]
  blocked_partitions: string[]
  partition_status: {
    partition: string
    field: string
    readable: boolean
    verdict: string
    null_ratio: string
    null_threshold: string
    block_level: string
  }[]
  store_write_status: string
  generated_at: string
}

export type RecheckResult = {
  release_no: string
  frozen: {
    rule_version: number
    conclusion: string
    readable_partitions: string[]
    recomputed_conclusion: string
    recomputed_readable: string[]
    matches_record: boolean
  }
  current_rule: {
    rule_version: number
    conclusion: string
    readable_partitions: string[]
    advisory_only: boolean
    note: string
  } | null
  silently_flipped: boolean
  would_flip_partitions?: string[]
}
