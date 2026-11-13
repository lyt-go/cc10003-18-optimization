<template>
  <section class="page" data-module="attribution">
    <header class="page-head">
      <div>
        <h2>归因模型管理</h2>
        <p class="page-desc">
          定义渠道和触点的归因规则；发布入口支持多选批量提交，整组契约完整后才会生成或复用数据服务版本。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记归因规则</button>
        <button class="btn" type="button" @click="exportRows">导出归因模型清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="toolbar">
      <label class="field">
        <span>批量动作</span>
        <select v-model="batchAction">
          <option v-for="action in actions" :key="action" :value="action">{{ action }}</option>
        </select>
      </label>
      <button class="btn" type="button" :disabled="!selectedIds.length" @click="runBatchAction">
        对所选 {{ selectedIds.length }} 条执行
      </button>
      <button class="btn primary" type="button" :disabled="!selectedIds.length" @click="fillPublishRules">
        以所选规则发布契约
      </button>
      <span class="hint">已选 {{ selectedIds.length }} / {{ rows.length }} 条</span>
      <span v-if="batchMessage" :class="batchOk ? 'ok-text' : 'error-text'">{{ batchMessage }}</span>
    </div>

    <div class="scroll-x">
      <table class="data-table">
        <thead>
          <tr>
            <th class="check-col">
              <input type="checkbox" :checked="allSelected" @change="toggleAll($event)" />
            </th>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)" :class="{ selected: selectedIds.includes(Number(row.id)) }">
            <td class="check-col">
              <input type="checkbox" :checked="selectedIds.includes(Number(row.id))" @change="toggleRow(row)" />
            </td>
            <td v-for="column in columns" :key="column">
              <span v-if="column === '规则版本'">v{{ row[column] ?? '—' }}</span>
              <span v-else-if="column === '版本状态'" :class="['tag', row[column] === '已发布' ? 'ok' : '']">
                {{ row[column] ?? '—' }}
              </span>
              <span v-else-if="column === '可用状态'" :class="['tag', row[column] === '可用' ? 'ok' : 'bad']">
                {{ row[column] ?? '—' }}
              </span>
              <template v-else>{{ row[column] ?? '—' }}</template>
            </td>
            <td class="row-actions">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 2" class="empty-state">暂无归因模型数据，可先登记归因规则</td>
          </tr>
        </tbody>
      </table>
    </div>

    <section class="panel" data-panel="publish">
      <div class="panel-head">
        <h3 class="panel-title">整组契约发布</h3>
        <span class="hint">契约键 = 归因规则版本 + 返回字段 + 请求参数；相同契约只保留一份服务版本</span>
      </div>
      <div class="toolbar">
        <label class="field">
          <span>服务编号</span>
          <input v-model="publishForm['服务编号']" placeholder="DELI-0003" />
        </label>
        <label class="field">
          <span>数据集名称</span>
          <input v-model="publishForm['数据集名称']" placeholder="归因结果明细" />
        </label>
        <label class="field">
          <span>查询参数定义</span>
          <input v-model="publishForm['查询参数定义']" placeholder="日期区间,渠道,归因规则编号" />
        </label>
        <label class="field">
          <span>返回字段</span>
          <input v-model="publishForm['返回字段']" placeholder="日期,渠道,归因规则编号,转化金额" />
        </label>
        <label class="field">
          <span>速率限制</span>
          <input v-model="publishForm['速率限制']" placeholder="60 次/天" />
        </label>
        <label class="field">
          <span>分页最大条数</span>
          <input v-model.number="publishForm['分页最大条数']" type="number" min="1" />
        </label>
        <label class="field">
          <span>消费方应用</span>
          <input v-model="publishForm['消费方应用']" placeholder="经营分析平台" />
        </label>
        <label class="field">
          <span>当前版本（并发校验）</span>
          <input v-model="publishForm['当前版本']" placeholder="自动取当前服务版本" />
        </label>        <label class="field">
          <span>请求编号（幂等）</span>
          <input v-model="publishForm['请求编号']" placeholder="PUB-20261113-01" />
        </label>
      </div>
      <div class="toolbar">
        <button class="btn primary" type="button" :disabled="!publishRuleCodes.length || publishing" @click="publishBatch">
          {{ publishing ? '发布中…' : `发布 ${publishRuleCodes.length} 条规则的整组契约` }}
        </button>
        <span class="hint">待发布规则：{{ publishRuleCodes.join('、') || '请在表格中勾选规则' }}</span>
      </div>
      <p v-if="publishMessage" :class="publishOk ? 'ok-text' : 'error-text'">{{ publishMessage }}</p>
      <ul v-if="publishResults.length" class="conclusion-list">
        <li v-for="item in publishResults" :key="String(item.id ?? item.规则编号)">
          <span :class="item.ok ? 'ok-text' : 'error-text'">{{ item.ok ? '通过' : '未通过' }}</span>
          {{ item.规则编号 ?? item.id }} · v{{ item.规则版本 ?? '—' }} · {{ item.message }}
        </li>
      </ul>
      <p v-if="lastContract" class="hint">
        契约 {{ lastContract.contract_key }} → 服务版本 v{{ lastContract.version }}；
        {{ lastContract.reused ? '复用已有契约' : '生成新版本' }}；
        规则版本 {{ lastContract.rule_versions }}；本次失效页段 {{ lastContract.invalidated }} 个。
      </p>
      <p v-if="lastConflict" class="warn-text">
        冲突版本 {{ lastConflict['冲突编号'] }}：{{ lastConflict['来源'] }}，期望
        v{{ lastConflict['期望版本'] ?? '—' }}，当前 v{{ lastConflict['当前版本'] }}。
      </p>
    </section>

    <section class="panel" data-panel="contracts">
      <div class="panel-head">
        <h3 class="panel-title">服务契约与复用</h3>
        <span class="hint">契约数量 {{ stats['契约数量'] ?? 0 }} · 复用次数合计 {{ stats['契约复用数量'] ?? 0 }}</span>
      </div>
      <div class="scroll-x">
        <table class="data-table">
          <thead>
            <tr>
              <th>契约键</th><th>服务编号</th><th>服务版本</th><th>规则版本</th><th>返回字段</th>
              <th>条数上限</th><th>复用次数</th><th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="contract in contracts" :key="String(contract['契约键'])">
              <td>{{ contract['契约键'] }}</td>
              <td>{{ contract['服务编号'] }}</td>
              <td>v{{ contract['服务版本'] }}</td>
              <td>{{ contract['规则版本'] }}</td>
              <td>{{ (contract['返回字段'] ?? []).join('、') }}</td>
              <td>{{ contract['分页最大条数'] }}</td>
              <td>{{ contract['复用次数'] }}</td>
              <td><span :class="['tag', contract['当前'] ? 'ok' : '']">{{ contract['当前'] ? '当前' : contract['状态'] }}</span></td>
            </tr>
            <tr v-if="!contracts.length">
              <td colspan="8" class="empty-state">还没有已发布契约</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" data-panel="segments">
      <div class="panel-head">
        <h3 class="panel-title">页段结果与失效范围</h3>
        <span class="hint">失效页段 {{ stats['失效页段'] ?? 0 }} · 冲突版本 {{ stats['冲突版本'] ?? 0 }}</span>
      </div>
      <div class="scroll-x">
        <table class="data-table">
          <thead>
            <tr>
              <th>页段编号</th><th>服务编号</th><th>服务版本</th><th>契约键</th><th>页码</th>
              <th>条数</th><th>状态</th><th>失效原因</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="segment in segments" :key="String(segment['页段编号'])">
              <td>{{ segment['页段编号'] }}</td>
              <td>{{ segment['服务编号'] }}</td>
              <td>v{{ segment['服务版本'] }}</td>
              <td>{{ segment['契约键'] }}</td>
              <td>{{ segment['页码'] }}</td>
              <td>{{ segment['条数'] }}</td>
              <td><span :class="['tag', segment['状态'] === '有效' ? 'ok' : 'bad']">{{ segment['状态'] }}</span></td>
              <td>{{ segment['失效原因'] ?? '—' }}</td>
            </tr>
            <tr v-if="!segments.length">
              <td colspan="8" class="empty-state">还没有读取页段</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="conflicts.length" class="scroll-x" style="margin-top: 10px">
        <table class="data-table">
          <thead>
            <tr><th>冲突编号</th><th>服务编号</th><th>来源</th><th>期望版本</th><th>当前版本</th><th>时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="conflict in conflicts" :key="String(conflict['冲突编号'])">
              <td>{{ conflict['冲突编号'] }}</td>
              <td>{{ conflict['服务编号'] }}</td>
              <td>{{ conflict['来源'] }}</td>
              <td>{{ conflict['期望版本'] ?? '—' }}</td>
              <td>{{ conflict['当前版本'] }}</td>
              <td>{{ conflict['时间'] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" data-panel="audit">
      <div class="panel-head">
        <h3 class="panel-title">审计入口</h3>
        <span class="hint">每次发布 / 复用 / 换版 / 冲突都记录规则版本</span>
      </div>
      <div class="scroll-x">
        <table class="data-table">
          <thead>
            <tr><th>时间</th><th>动作</th><th>服务编号</th><th>服务版本</th><th>契约键</th><th>规则版本</th><th>结论</th></tr>
          </thead>
          <tbody>
            <tr v-for="log in auditLogs" :key="String(log.id)">
              <td>{{ log['时间'] }}</td>
              <td>{{ log['动作'] }}</td>
              <td>{{ log['服务编号'] ?? '—' }}</td>
              <td>{{ log['服务版本'] ? `v${log['服务版本']}` : '—' }}</td>
              <td>{{ log['契约键'] ?? '—' }}</td>
              <td>{{ log['规则版本'] ?? '—' }}</td>
              <td>{{ log['结论'] }}</td>
            </tr>
            <tr v-if="!auditLogs.length">
              <td colspan="7" class="empty-state">暂无审计记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条归因模型记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | null>
type ContractResult = {
  ok: boolean
  reused: boolean
  message: string
  contract_key: string
  version: number
  rule_versions: string
  invalidated: number
  invalidated_segments: string[]
}
type PublishConclusion = { id?: number; 规则编号?: string; 规则版本?: number; ok: boolean; message: string }
type PublishResponse = {
  ok: boolean
  message: string
  results: PublishConclusion[]
  contract?: ContractResult | null
  conflict?: Record<string, string | number | null> | null
}
type Contract = Record<string, any>
type Segment = Record<string, any>

const ENDPOINT = '/api/attribution'
const columns = ['规则编号', '归因类型', '回溯窗口天', '规则版本', '版本状态', '可用状态', '关联渠道']
const actions = ['提交规则评审', '发布规则', '下线规则']
const filterFields = ['规则编号', '归因类型']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const selectedIds = ref<number[]>([])
const batchAction = ref('发布规则')
const batchMessage = ref('')
const batchOk = ref(false)

const stats = ref<Record<string, number>>({})
const contracts = ref<Contract[]>([])
const segments = ref<Segment[]>([])
const conflicts = ref<Contract[]>([])
const auditLogs = ref<Contract[]>([])

const publishRuleCodes = ref<string[]>([])
const publishing = ref(false)
const publishMessage = ref('')
const publishOk = ref(false)
const publishResults = ref<PublishConclusion[]>([])
const lastContract = ref<ContractResult | null>(null)
const lastConflict = ref<Record<string, any> | null>(null)
const publishForm = reactive<Record<string, any>>({
  服务编号: 'DELI-0003',
  数据集名称: '归因结果明细',
  查询参数定义: '日期区间,渠道,归因规则编号',
  返回字段: '日期,渠道,归因规则编号,触点数量,转化数量,转化金额',
  速率限制: '60 次/天',
  分页最大条数: 200,
  消费方应用: '经营分析平台',
  当前版本: '',
  请求编号: '',
})

const statCards = computed(() => [
  { label: '归因规则总数', value: total.value },
  { label: '契约数量', value: stats.value['契约数量'] ?? 0 },
  { label: '契约复用数量', value: stats.value['契约复用数量'] ?? 0 },
  { label: '失效页段', value: stats.value['失效页段'] ?? 0 },
  { label: '冲突版本', value: stats.value['冲突版本'] ?? 0 },
])

const allSelected = computed(() => rows.value.length > 0 && selectedIds.value.length === rows.value.length)
const currentService = computed(() => String(publishForm['服务编号'] ?? '').trim())

function toggleAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selectedIds.value = checked ? rows.value.map((row) => Number(row.id)) : []
}

function toggleRow(row: Row) {
  const id = Number(row.id)
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((item) => item !== id)
    : [...selectedIds.value, id]
}

function fillPublishRules() {
  publishRuleCodes.value = rows.value
    .filter((row) => selectedIds.value.includes(Number(row.id)))
    .map((row) => String(row['规则编号'] ?? ''))
    .filter(Boolean)
  publishMessage.value = '已将所选规则填入整组契约发布'
  publishOk.value = true
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '归因规则登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? '归因模型动作未生效，请稍后重试')
    }
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归因模型操作失败'
  }
}

async function runBatchAction() {
  batchMessage.value = ''
  try {
    const payload = await fetchJson<PublishResponse & { results: PublishConclusion[] }>(
      `${ENDPOINT}/batch/actions`,
      {
        method: 'POST',
        body: JSON.stringify({ ids: selectedIds.value, action: batchAction.value }),
      },
    )
    batchOk.value = payload.ok
    batchMessage.value = payload.message
    await refreshAll()
  } catch (error) {
    batchOk.value = false
    batchMessage.value = error instanceof Error ? error.message : '批量动作失败'
  }
}

async function publishBatch() {
  if (!publishRuleCodes.value.length) {
    return
  }
  publishing.value = true
  publishMessage.value = ''
  publishResults.value = []
  lastContract.value = null
  lastConflict.value = null
  try {
    const values: Record<string, any> = { ...publishForm, 规则编号列表: publishRuleCodes.value }
    if (values['当前版本'] === '' || values['当前版本'] === null) {
      delete values['当前版本']
    } else {
      values['当前版本'] = Number(values['当前版本'])
    }
    if (!values['请求编号']) {
      delete values['请求编号']
    }
    const payload = await fetchJson<PublishResponse>(`${ENDPOINT}/publish/batch`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    publishOk.value = payload.ok
    publishMessage.value = payload.message
    publishResults.value = payload.results ?? []
    lastContract.value = payload.contract ?? null
    lastConflict.value = payload.conflict ?? null
    await refreshAll()
  } catch (error) {
    publishOk.value = false
    publishMessage.value = error instanceof Error ? error.message : '整组契约发布失败'
  } finally {
    publishing.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('归因规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    selectedIds.value = selectedIds.value.filter((id) => rows.value.some((row) => Number(row.id) === id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归因模型列表读取失败'
  }
}

async function refreshContractViews() {
  const [statsPayload, contractPayload, segmentPayload, conflictPayload, auditPayload] = await Promise.all([
    fetchJson<Record<string, number>>('/api/delivery/stats'),
    fetchJson<{ items: Contract[] }>('/api/delivery/contracts'),
    fetchJson<{ items: Segment[] }>('/api/delivery/segments?size=50'),
    fetchJson<{ items: Contract[] }>('/api/delivery/conflicts?size=50'),
    fetchJson<{ items: Contract[] }>('/api/attribution/audit/logs?size=20'),
  ])
  stats.value = statsPayload
  contracts.value = contractPayload.items ?? []
  segments.value = segmentPayload.items ?? []
  conflicts.value = conflictPayload.items ?? []
  auditLogs.value = auditPayload.items ?? []
  // 当前版本随契约视图刷新：表单默认带上调用方读到的版本，
  // 并发时后端以此拒绝第二个版本，界面上也能看到冲突。
  const current = contracts.value.find(
    (item) => item['当前'] && item['服务编号'] === currentService.value,
  )
  if (current) {
    publishForm['当前版本'] = String(current['服务版本'])
  }
}

async function refreshAll() {
  await reload()
  try {
    await refreshContractViews()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '契约视图读取失败'
  }
}

onMounted(refreshAll)
</script>
