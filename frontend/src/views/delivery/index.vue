<template>
  <section class="page" data-module="delivery">
    <header class="page-head">
      <div>
        <h2>数据服务管理</h2>
        <p class="page-desc">
          管理和发布数据服务接口；返回字段或请求参数变化才生成新服务版本，相同契约复用同一版本并使旧页段失效。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记服务版本</button>
        <button class="btn" type="button" @click="exportRows">导出数据服务清单</button>
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

    <div class="scroll-x">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>契约键</th>
            <th>规则版本</th>
            <th>复用次数</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">
              <span v-if="column === '版本号'">v{{ row[column] ?? '—' }}</span>
              <template v-else>{{ row[column] ?? '—' }}</template>
            </td>
            <td>{{ row['契约键'] ?? '—' }}</td>
            <td>{{ row['规则版本'] ?? '—' }}</td>
            <td>{{ row['契约复用次数'] ?? 0 }}</td>
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
            <td :colspan="columns.length + 4" class="empty-state">暂无数据服务数据，可先登记服务版本</td>
          </tr>
        </tbody>
      </table>
    </div>

    <section class="panel" data-panel="read">
      <div class="panel-head">
        <h3 class="panel-title">分页读取</h3>
        <span class="hint">首段从第 1 页开始；后续页段带同一会话编号，超上限在入口拒绝</span>
      </div>
      <div class="toolbar">
        <label class="field">
          <span>服务编号</span>
          <input v-model="readForm['服务编号']" placeholder="DELI-0003" />
        </label>
        <label class="field">
          <span>契约键（留空取当前）</span>
          <input v-model="readForm['契约键']" placeholder="CTK-..." />
        </label>
        <label class="field">
          <span>页码</span>
          <input v-model.number="readForm['页码']" type="number" min="1" />
        </label>
        <label class="field">
          <span>条数</span>
          <input v-model.number="readForm['条数']" type="number" min="1" />
        </label>
        <label class="field">
          <span>会话编号</span>
          <input v-model="readForm['会话编号']" placeholder="首段返回后自动填入" />
        </label>
        <label class="field">
          <span>消费方</span>
          <input v-model="readForm['消费方应用']" placeholder="留空取契约配置" />
        </label>
        <label class="field">
          <span>请求编号（幂等）</span>
          <input v-model="readForm['请求编号']" placeholder="READ-001" />
        </label>
        <button class="btn primary" type="button" @click="readPage">读取</button>
        <button class="btn" type="button" @click="nextPage">读取下一页</button>
        <button class="btn ghost" type="button" @click="startOver">从首段重新开始</button>
      </div>
      <p v-if="readMessage" :class="readOk ? 'ok-text' : 'error-text'">{{ readMessage }}</p>
      <p v-if="readMeta" class="hint">{{ readMeta }}</p>
      <div v-if="readRows.length" class="scroll-x">
        <table class="data-table">
          <thead>
            <tr><th v-for="field in readFields" :key="field">{{ field }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in readRows" :key="index">
              <td v-for="field in readFields" :key="field">{{ item[field] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else-if="readOk" class="hint">本次读取没有返回数据行</p>
    </section>

    <section class="panel" data-panel="contracts">
      <div class="panel-head">
        <h3 class="panel-title">服务契约</h3>
        <span class="hint">契约数量 {{ stats['契约数量'] ?? 0 }} · 复用次数 {{ stats['契约复用数量'] ?? 0 }}</span>
      </div>
      <div class="scroll-x">
        <table class="data-table">
          <thead>
            <tr>
              <th>契约键</th><th>服务编号</th><th>服务版本</th><th>规则版本</th><th>返回字段</th>
              <th>请求参数</th><th>条数上限</th><th>速率限制</th><th>复用次数</th><th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="contract in contracts" :key="String(contract['契约键'])">
              <td>{{ contract['契约键'] }}</td>
              <td>{{ contract['服务编号'] }}</td>
              <td>v{{ contract['服务版本'] }}</td>
              <td>{{ contract['规则版本'] }}</td>
              <td>{{ (contract['返回字段'] ?? []).join('、') }}</td>
              <td>{{ contract['查询参数定义'] }}</td>
              <td>{{ contract['分页最大条数'] }}</td>
              <td>{{ contract['速率限制'] }}</td>
              <td>{{ contract['复用次数'] }}</td>
              <td><span :class="['tag', contract['当前'] ? 'ok' : '']">{{ contract['当前'] ? '当前' : contract['状态'] }}</span></td>
            </tr>
            <tr v-if="!contracts.length">
              <td colspan="10" class="empty-state">还没有已发布契约，请到归因模型页批量发布</td>
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
      <div class="toolbar">
        <label class="field">
          <span>按状态</span>
          <select v-model="segmentStatus" @change="loadSegments">
            <option value="">全部</option>
            <option value="有效">有效</option>
            <option value="已失效">已失效</option>
          </select>
        </label>
        <span class="hint">按契约键 / 状态查看失效范围，换版原因会写在失效原因里</span>
      </div>
      <div class="scroll-x">
        <table class="data-table">
          <thead>
            <tr>
              <th>页段编号</th><th>服务编号</th><th>服务版本</th><th>契约键</th><th>页码</th>
              <th>起始-结束</th><th>状态</th><th>失效原因</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="segment in segments" :key="String(segment['页段编号'])">
              <td>{{ segment['页段编号'] }}</td>
              <td>{{ segment['服务编号'] }}</td>
              <td>v{{ segment['服务版本'] }}</td>
              <td>{{ segment['契约键'] }}</td>
              <td>{{ segment['页码'] }}</td>
              <td>{{ segment['起始位置'] }}-{{ segment['结束位置'] }}</td>
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
        <span class="hint">发布、复用、换版、冲突与读取不可用都记录规则版本</span>
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
      <span>共 {{ total }} 条数据服务记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | null>
type Contract = Record<string, any>
type Segment = Record<string, any>

type ReadResponse = {
  ok: boolean
  可用?: boolean
  code?: string
  message: string
  契约键?: string
  服务版本?: number
  规则版本?: string
  会话编号?: string
  页码?: number
  条数?: number
  单次返回上限?: number
  下一页?: number
  总条数?: number
  需要重新开始?: boolean
  items?: Record<string, any>[]
}

const ENDPOINT = '/api/delivery'
const columns = ['服务编号', '数据集名称', '查询参数定义', '返回字段', '速率限制', '分页最大条数', '版本号', '消费方应用']
const actions = ['提交服务评审', '发布服务', '下线服务']
const filterFields = columns.slice(0, 2)

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})

const stats = ref<Record<string, number>>({})
const contracts = ref<Contract[]>([])
const segments = ref<Segment[]>([])
const conflicts = ref<Contract[]>([])
const auditLogs = ref<Contract[]>([])
const segmentStatus = ref('')

const readForm = reactive<Record<string, any>>({
  服务编号: 'DELI-0003',
  契约键: '',
  页码: 1,
  条数: 200,
  会话编号: '',
  消费方应用: '',
  请求编号: '',
})
const readRows = ref<Record<string, any>[]>([])
const readMessage = ref('')
const readOk = ref(false)
const readMeta = ref('')
const activeContractKey = ref('')
const readFields = ref<string[]>([])

const statCards = computed(() => [
  { label: '服务版本总数', value: total.value },
  { label: '契约数量', value: stats.value['契约数量'] ?? 0 },
  { label: '契约复用数量', value: stats.value['契约复用数量'] ?? 0 },
  { label: '失效页段', value: stats.value['失效页段'] ?? 0 },
  { label: '冲突版本', value: stats.value['冲突版本'] ?? 0 },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '服务版本登记入口尚未接入审批流，可在归因模型页批量发布契约'
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
      throw new Error(payload.message ?? '数据服务动作未生效，请稍后重试')
    }
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据服务操作失败'
  }
}

function buildReadPayload() {
  const values: Record<string, any> = {}
  for (const [key, value] of Object.entries(readForm)) {
    if (value === '' || value === null || value === undefined) {
      continue
    }
    values[key] = value
  }
  values['页码'] = Number(values['页码'] ?? 1)
  values['条数'] = Number(values['条数'] ?? 200)
  if (!values['契约键'] && activeContractKey.value) {
    values['契约键'] = activeContractKey.value
  }
  return values
}

async function readPage() {
  readMessage.value = ''
  readRows.value = []
  try {
    const response = await request(`${ENDPOINT}/read`, {
      method: 'POST',
      body: JSON.stringify({ values: buildReadPayload() }),
    })
    const payload = (await response.json()) as ReadResponse
    readOk.value = Boolean(payload.ok)
    readMessage.value = payload.message ?? ''
    if (!payload.ok) {
      readRows.value = []
      readMeta.value = payload.code ? `错误码 ${payload.code}` : ''
      if (payload.需要重新开始) {
        readForm['页码'] = 1
        readForm['会话编号'] = ''
      }
      return
    }
    readRows.value = payload.items ?? []
    readFields.value = payload.items?.length ? Object.keys(payload.items[0]) : []
    activeContractKey.value = payload.契约键 ?? ''
    readForm['契约键'] = payload.契约键 ?? ''
    readForm['会话编号'] = payload.会话编号 ?? ''
    readForm['页码'] = payload.下一页 ?? (payload.页码 ?? 1) + 1
    readForm['条数'] = payload.条数 ?? readForm['条数']
    readMeta.value =
      `契约 ${payload.契约键} · 服务版本 v${payload.服务版本} · 规则版本 ${payload.规则版本}` +
      ` · 本页 ${payload.条数} 条 / 上限 ${payload.单次返回上限} 条 · 总条数 ${payload.总条数}`
    await refreshViews()
  } catch (error) {
    readOk.value = false
    readMessage.value = error instanceof Error ? error.message : '读取失败，明确不可用'
  }
}

async function nextPage() {
  await readPage()
}

function startOver() {
  readForm['页码'] = 1
  readForm['会话编号'] = ''
  readForm['请求编号'] = ''
  readMessage.value = '已回到首段，请重新读取'
  readRows.value = []
  readMeta.value = ''
}

async function loadSegments() {
  const params = new URLSearchParams({ size: '50' })
  if (segmentStatus.value) {
    params.set('状态', segmentStatus.value)
  }
  const payload = await fetchJson<{ items: Segment[] }>(`${ENDPOINT}/segments?${params.toString()}`)
  segments.value = payload.items ?? []
}

async function refreshViews() {
  const [statsPayload, contractPayload, conflictPayload, auditPayload] = await Promise.all([
    fetchJson<Record<string, number>>(`${ENDPOINT}/stats`),
    fetchJson<{ items: Contract[] }>(`${ENDPOINT}/contracts`),
    fetchJson<{ items: Contract[] }>(`${ENDPOINT}/conflicts?size=50`),
    fetchJson<{ items: Contract[] }>(`${ENDPOINT}/audit/logs?size=20`),
  ])
  stats.value = statsPayload
  contracts.value = contractPayload.items ?? []
  conflicts.value = conflictPayload.items ?? []
  auditLogs.value = auditPayload.items ?? []
  await loadSegments()
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('服务版本列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据服务列表读取失败'
  }
}

async function refreshAll() {
  await reload()
  try {
    await refreshViews()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '契约视图读取失败'
  }
}

onMounted(refreshAll)
</script>
