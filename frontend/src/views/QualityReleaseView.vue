<template>
  <section class="page" data-module="quality_release">
    <header class="page-head">
      <div>
        <h2>质量放行</h2>
        <p class="page-desc">
          按分区校验待入库批次的空值与异常值，记录抽样分母、实测比例和阻断级别；
          同步存储清单并只把可读分区写入特征存储。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="qr-refresh" @click="refresh">刷新</button>
      </div>
    </header>

    <section class="qr-panel">
      <h3>适用规则版本</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>规则编号</th><th>版本</th><th>目标特征 / 字段</th>
            <th>空值阈值</th><th>异常值阈值</th><th>阻断级别</th><th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rule in rules" :key="`${rule.规则编号}-${rule.版本}`" :data-testid="`qr-rule-${rule.规则编号}-${rule.版本}`">
            <td>{{ rule.规则编号 }}</td>
            <td>v{{ rule.版本 }}</td>
            <td>{{ rule.目标特征 }} / {{ rule.检查字段 }}</td>
            <td>{{ rule.空值阈值 }}</td>
            <td>{{ rule.异常值阈值 }}</td>
            <td>{{ rule.阻断级别 }}</td>
            <td>{{ rule.状态 }}</td>
          </tr>
          <tr v-if="!rules.length"><td colspan="7" class="empty-state">暂无规则版本</td></tr>
        </tbody>
      </table>
    </section>

    <section class="qr-panel">
      <h3>提交分区校验</h3>
      <form class="qr-form" @submit.prevent="runPreview">
        <label>
          <span>入库批次</span>
          <input v-model="form.batchNo" data-testid="qr-batch" required />
        </label>
        <label>
          <span>适用规则</span>
          <select v-model="form.ruleKey" data-testid="qr-rule-select">
            <option v-for="rule in rules.filter((item) => item.状态 === '生效中')" :key="`${rule.规则编号}-${rule.版本}`" :value="`${rule.规则编号}#${rule.版本}`">
              {{ rule.规则编号 }} · v{{ rule.版本 }}
            </option>
          </select>
        </label>
        <label>
          <span>放行范围（留空=全部分区，分号分隔）</span>
          <input v-model="form.scope" data-testid="qr-scope" placeholder="dt=2026-11-01;dt=2026-11-03" />
        </label>
        <button class="btn" type="submit" data-testid="qr-preview">预校验</button>
        <button class="btn primary" type="button" data-testid="qr-submit" :disabled="submitting" @click="runSubmit">
          {{ submitting ? '提交中…' : '提交放行' }}
        </button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>分区</th><th>抽样分母</th><th>空值数</th><th>空值比例</th>
            <th>异常值数</th><th>异常值比例</th><th>本地判定</th><th>可读</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in form.partitions" :key="index" :data-testid="`qr-partition-${index}`">
            <td><input v-model="row.partition" class="qr-cell-input" :data-testid="`qr-partition-name-${index}`" /></td>
            <td><input v-model.number="row.denominator" type="number" min="0" class="qr-cell-input" :data-testid="`qr-partition-denominator-${index}`" /></td>
            <td><input v-model.number="row.null_count" type="number" min="0" class="qr-cell-input" :data-testid="`qr-partition-null-${index}`" /></td>
            <td>{{ outcomeFor(index)?.null_ratio_text ?? '—' }}</td>
            <td><input v-model.number="row.outlier_count" type="number" min="0" class="qr-cell-input" :data-testid="`qr-partition-outlier-${index}`" /></td>
            <td>{{ outcomeFor(index)?.outlier_ratio_text ?? '—' }}</td>
            <td>{{ outcomeFor(index)?.verdict ?? '—' }}</td>
            <td>{{ outcomeFor(index) ? (outcomeFor(index)!.readable ? '是' : '否') : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <div class="page-actions">
        <button class="btn ghost" type="button" data-testid="qr-add-partition" @click="addPartition">增加分区</button>
        <button class="btn ghost" type="button" data-testid="qr-remove-partition" @click="removePartition">删除末行</button>
      </div>

      <div v-if="preview" class="qr-result" data-testid="qr-preview-result">
        <strong>预校验结论：{{ preview.conclusion }}</strong>
        <span>可读分区：{{ preview.readable_partitions.join('、') || '无' }}</span>
        <span>阻断分区：{{ preview.blocked_partitions.join('、') || '无' }}</span>
        <span>覆盖率：{{ preview.coverage_ratio_text }}</span>
        <p v-if="preview.hold_reason" class="error-text">{{ preview.hold_reason }}</p>
      </div>
    </section>

    <section class="qr-panel">
      <h3>放行记录</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>放行编号</th><th>入库批次</th><th>规则版本</th><th>结论</th>
            <th>可读分区</th><th>阻断分区</th><th>覆盖率</th><th>存储写入</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in releases" :key="row.放行编号" :data-testid="`qr-release-${row.放行编号}`" :data-status="row.status">
            <td>{{ row.放行编号 }}</td>
            <td>{{ row.入库批次 }}</td>
            <td>v{{ row.规则版本 }}</td>
            <td>{{ row.结论 }}</td>
            <td>{{ row.可读分区 || '—' }}</td>
            <td>{{ row.阻断分区 || '—' }}</td>
            <td>{{ row.覆盖率 }}</td>
            <td>{{ row.存储写入状态 }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`qr-open-${row.放行编号}`" @click="openRelease(row.放行编号)">查看</button>
              <button class="link" type="button" :data-testid="`qr-recheck-${row.放行编号}`" @click="recheck(row.放行编号)">复核</button>
            </td>
          </tr>
          <tr v-if="!releases.length"><td colspan="9" class="empty-state">暂无放行记录</td></tr>
        </tbody>
      </table>
    </section>

    <section v-if="detail" class="qr-panel" data-testid="qr-detail">
      <h3>放行详情 · {{ detail.放行编号 }}</h3>
      <p>
        结论 <strong>{{ detail.结论 }}</strong> · 规则 {{ detail.规则编号 }} v{{ detail.规则版本 }}
        · 冻结空值阈值 {{ detail.规则快照.null_threshold_text }} · 覆盖率 {{ detail.覆盖率 }}
      </p>
      <p v-if="detail.扣留原因" class="error-text">{{ detail.扣留原因 }}</p>

      <h4>分区结果</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>分区</th><th>字段</th><th>抽样分母</th><th>空值数 / 比例</th>
            <th>异常值数 / 比例</th><th>触发</th><th>判定</th><th>可读</th><th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="part in detail.分区结果" :key="part.partition" :data-testid="`qr-detail-partition-${part.partition}`">
            <td>{{ part.partition }}</td>
            <td>{{ part.field }}</td>
            <td>{{ part.denominator }}</td>
            <td>{{ part.null_count }} / {{ part.null_ratio_text }}</td>
            <td>{{ part.outlier_count }} / {{ part.outlier_ratio_text }}</td>
            <td>{{ part.triggered.join('、') || '—' }}</td>
            <td>{{ part.verdict }}</td>
            <td>{{ part.readable ? '是' : '否' }}</td>
            <td>{{ part.reason }}</td>
          </tr>
        </tbody>
      </table>

      <h4>存储清单</h4>
      <pre class="qr-manifest" data-testid="qr-manifest">{{ JSON.stringify(detail.存储清单, null, 2) }}</pre>
    </section>

    <section v-if="recheckResult" class="qr-panel" data-testid="qr-recheck-result">
      <h3>历史复核 · {{ recheckResult.release_no }}</h3>
      <p>
        冻结 v{{ recheckResult.frozen.rule_version }} 复核结论：
        <strong>{{ recheckResult.frozen.recomputed_conclusion }}</strong>
        （与记录一致：{{ recheckResult.frozen.matches_record ? '是' : '否' }}）
      </p>
      <p v-if="recheckResult.current_rule">
        当前规则 v{{ recheckResult.current_rule.rule_version }} 仅供参考，会给出的结论：
        {{ recheckResult.current_rule.conclusion }}。历史记录不会被改写。
      </p>
      <p v-if="recheckResult.silently_flipped" class="error-text">
        若按新阈值，此前被拒绝的分区 {{ (recheckResult.would_flip_partitions ?? []).join('、') }}
        会变为可读；需重新提交放行，而不是静默翻转。
      </p>
    </section>

    <p v-if="errorMessage" class="error-text" data-testid="qr-error">{{ errorMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import {
  getRelease,
  listReleases,
  listRuleVersions,
  previewRelease,
  recheckRelease,
  submitRelease,
} from '@/api/quality-release'
import type {
  BatchOutcome,
  PartitionInput,
  RecheckResult,
  ReleaseDetail,
  ReleaseRecord,
  RuleVersion,
} from '@/data/quality-release'

type EditablePartition = PartitionInput

const rules = ref<RuleVersion[]>([])
const releases = ref<ReleaseRecord[]>([])
const preview = ref<BatchOutcome | null>(null)
const detail = ref<ReleaseDetail | null>(null)
const recheckResult = ref<RecheckResult | null>(null)
const errorMessage = ref('')
const submitting = ref(false)
const form = reactive({
  batchNo: 'BATCH-20261103',
  ruleKey: '',
  scope: '',
  partitions: [
    { partition: 'dt=2026-11-01', denominator: 100, null_count: 5, outlier_count: 0 },
    { partition: 'dt=2026-11-02', denominator: 0, null_count: 0, outlier_count: 0 },
    { partition: 'dt=2026-11-03', denominator: 100, null_count: 6, outlier_count: 0 },
  ] as EditablePartition[],
})

function outcomeFor(index: number) {
  const partition = form.partitions[index]?.partition
  return preview.value?.partitions.find((item) => item.partition === partition)
}

function rulePayload() {
  const [ruleNo, version] = form.ruleKey.split('#')
  return {
    rule_no: ruleNo || '',
    rule_version: version ? Number(version) : null,
  }
}

function submitPayload() {
  const scope = form.scope
    .split(';')
    .map((part) => part.trim())
    .filter(Boolean)
  return {
    ...rulePayload(),
    batch_no: form.batchNo,
    partitions: form.partitions.map((row) => ({ ...row })),
    scope: scope.length ? scope : undefined,
  }
}

function addPartition() {
  form.partitions.push({ partition: `dt=2026-11-0${form.partitions.length + 1}`, denominator: 0, null_count: 0, outlier_count: 0 })
}

function removePartition() {
  if (form.partitions.length > 1) form.partitions.pop()
}

async function refresh() {
  errorMessage.value = ''
  try {
    rules.value = await listRuleVersions()
    releases.value = await listReleases()
    const active = rules.value.find((rule) => rule.状态 === '生效中')
    if (active && !form.ruleKey) form.ruleKey = `${active.规则编号}#${active.版本}`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '质量放行数据读取失败'
  }
}

async function runPreview() {
  errorMessage.value = ''
  try {
    preview.value = await previewRelease(submitPayload())
  } catch (error) {
    preview.value = null
    errorMessage.value = error instanceof Error ? error.message : '预校验失败'
  }
}

async function runSubmit() {
  submitting.value = true
  errorMessage.value = ''
  try {
    detail.value = await submitRelease(submitPayload())
    preview.value = null
    await refresh()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交失败'
  } finally {
    submitting.value = false
  }
}

async function openRelease(releaseNo: string) {
  errorMessage.value = ''
  try {
    detail.value = await getRelease(releaseNo)
    recheckResult.value = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '放行详情读取失败'
  }
}

async function recheck(releaseNo: string) {
  errorMessage.value = ''
  try {
    recheckResult.value = await recheckRelease(releaseNo)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核失败'
  }
}

onMounted(refresh)
</script>
