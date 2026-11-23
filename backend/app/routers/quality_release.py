"""质量放行接口：规则版本、分区校验预览、放行提交、存储清单与历史复核。

和通用资源路由分开：这里的提交是「一份规则 + 一个范围」的原子操作，
不能用通用动作接口绕过阈值校验。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.domain.quality_release import ReleaseError
from app.services import quality_release as service

router = APIRouter(prefix="/api/quality-release", tags=["质量放行"])


class RuleVersionPayload(BaseModel):
    rule_no: str
    values: dict[str, Any] = Field(default_factory=dict)


class ReleasePayload(BaseModel):
    rule_no: str | None = None
    rule_version: int | None = None
    batch_no: str
    partitions: list[dict[str, Any]] = Field(default_factory=list)
    scope: list[str] | str | None = None


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/rules")
def list_rules() -> dict[str, Any]:
    items = service.list_rules()
    return {"items": items, "total": len(items)}


@router.post("/rules")
def publish_rule(payload: RuleVersionPayload) -> dict[str, Any]:
    try:
        row = service.publish_rule_version(payload.rule_no, payload.values)
    except ReleaseError as exc:
        raise _bad(exc) from exc
    return {"ok": True, "message": f"规则 {row['规则编号']} 已发布版本 {row['版本']}", "rule": row}


@router.get("/releases")
def list_releases() -> dict[str, Any]:
    items = service.list_releases()
    return {"items": items, "total": len(items)}


@router.post("/releases/preview")
def preview_release(payload: ReleasePayload) -> dict[str, Any]:
    """只算不落库：让离线消费者在提交前核对可读分区和拒绝证据。"""
    try:
        return service.preview_release(payload.model_dump())
    except ReleaseError as exc:
        raise _bad(exc) from exc


@router.post("/releases")
def submit_release(payload: ReleasePayload) -> dict[str, Any]:
    try:
        record = service.submit_release(payload.model_dump())
    except ReleaseError as exc:
        raise _bad(exc) from exc
    return {
        "ok": True,
        "message": f"放行记录 {record['放行编号']} 已提交：{record['结论']}",
        "release": record,
    }


@router.get("/releases/{release_no}")
def get_release(release_no: str) -> dict[str, Any]:
    try:
        return service.get_release(release_no)
    except service.ReleaseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/releases/{release_no}/manifest")
def get_manifest(release_no: str) -> dict[str, Any]:
    try:
        return service.get_manifest(release_no)
    except service.ReleaseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/releases/{release_no}/recheck")
def recheck_release(release_no: str) -> dict[str, Any]:
    """按冻结阈值复核历史记录；当前规则只给提示，绝不把拒绝记录改成通过。"""
    try:
        return service.recheck_release(release_no)
    except service.ReleaseNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReleaseError as exc:
        raise _bad(exc) from exc
