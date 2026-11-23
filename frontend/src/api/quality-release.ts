/** 质量放行接口封装：与通用资源接口分开，提交是原子操作。 */
import { request } from './client'
import type {
  BatchOutcome,
  RecheckResult,
  ReleaseDetail,
  ReleaseRecord,
  RuleVersion,
  StorageManifest,
} from '@/data/quality-release'

async function readJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { detail?: string; message?: string }
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || `接口返回 ${response.status}`)
  }
  return payload
}

export async function listRuleVersions(): Promise<RuleVersion[]> {
  const response = await request('/api/quality-release/rules')
  return (await readJson<{ items: RuleVersion[] }>(response)).items
}

export async function publishRuleVersion(
  ruleNo: string,
  values: Record<string, string | number>,
): Promise<RuleVersion> {
  const response = await request('/api/quality-release/rules', {
    method: 'POST',
    body: JSON.stringify({ rule_no: ruleNo, values }),
  })
  return (await readJson<{ rule: RuleVersion }>(response)).rule
}

export async function previewRelease(payload: {
  rule_no: string
  rule_version: number | null
  batch_no: string
  partitions: unknown[]
  scope?: string[]
}): Promise<BatchOutcome> {
  const response = await request('/api/quality-release/releases/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const body = await readJson<{ batch: BatchOutcome }>(response)
  return body.batch
}

export async function submitRelease(payload: {
  rule_no: string
  rule_version: number | null
  batch_no: string
  partitions: unknown[]
  scope?: string[]
}): Promise<ReleaseDetail> {
  const response = await request('/api/quality-release/releases', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return (await readJson<{ release: ReleaseDetail }>(response)).release
}

export async function listReleases(): Promise<ReleaseRecord[]> {
  const response = await request('/api/quality-release/releases')
  return (await readJson<{ items: ReleaseRecord[] }>(response)).items
}

export async function getRelease(releaseNo: string): Promise<ReleaseDetail> {
  const response = await request(`/api/quality-release/releases/${encodeURIComponent(releaseNo)}`)
  return await readJson<ReleaseDetail>(response)
}

export async function getManifest(releaseNo: string): Promise<StorageManifest> {
  const response = await request(
    `/api/quality-release/releases/${encodeURIComponent(releaseNo)}/manifest`,
  )
  return await readJson<StorageManifest>(response)
}

export async function recheckRelease(releaseNo: string): Promise<RecheckResult> {
  const response = await request(
    `/api/quality-release/releases/${encodeURIComponent(releaseNo)}/recheck`,
  )
  return await readJson<RecheckResult>(response)
}
