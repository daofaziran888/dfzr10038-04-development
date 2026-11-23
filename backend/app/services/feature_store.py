"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class FeatureStoreService(BaseModuleService):
    MODULE = "feature_store"
    ENTITY = "存储配置"
    OPERATION_TERM = "存储"
    LIST_FIELDS = [
        "配置编号", "特征列表", "存储引擎", "分区键", "TTL设置", "压缩策略",
        "存储路径", "存储体积", "可用分区", "不可用分区", "质量放行编号", "存储写入状态",
    ]
    REQUIRED_FIELDS = ["配置编号", "特征列表", "存储引擎"]
    STATUS_ORDER = ["草稿", "已启用", "容量告警", "已迁移"]
    ACTION_RULES = {"启用存储": "已启用", "标记告警": "容量告警", "提交迁移": "已迁移"}
    NEGATIVE_ACTIONS = []
