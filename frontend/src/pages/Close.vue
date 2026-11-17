<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const preview = ref(null)
const declared = ref(0)
const batches = ref([])
const msg = ref('')
const err = ref('')
const reason = ref('')

async function load() {
  preview.value = await api.closePreview()
  declared.value = preview.value.sealed && preview.value.current_batch
    ? preview.value.current_batch.declared_cash
    : preview.value.system_total
  batches.value = await api.batches()
}

const currentBatch = computed(() => preview.value?.current_batch || null)
const policy = computed(() => preview.value?.reseal_policy || 'forbid')
const canSeal = computed(() => !!preview.value?.can_seal)
const supersede = computed(() => policy.value === 'supersede')
const batchNoById = computed(() => {
  const m = {}
  for (const b of batches.value) m[b.id] = b.batch_no
  return m
})

async function seal() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.seal({
      business_day: preview.value.business_day,
      declared_cash: declared.value,
      supersede_reason: reason.value || null,
    })
    const covered = (r.superseded_batch_ids || [])
      .map((id) => batchNoById.value[id] || '#' + id)
      .join('、')
    msg.value = r.superseded
      ? `已重封 ${r.batch_no}：旧批 ${covered} 置为 superseded（只读保留），系统 ${r.system_total.toFixed(2)} / 实盘 ${r.declared_cash.toFixed(2)} / 长短款 ${r.variance.toFixed(2)}`
      : `已封账 ${r.batch_no}：系统 ${r.system_total.toFixed(2)} / 实盘 ${r.declared_cash.toFixed(2)} / 长短款 ${r.variance.toFixed(2)}`
    reason.value = ''
    await load()
  } catch (e) { err.value = e.message }
}

function batchState(b) {
  // 现行标记以后端收敛口径为准：同日多行 sealed 残留时只有一行是现行。
  const isCurrent = b.is_current ?? (b.status === 'sealed')
  if (isCurrent) return { text: '现行已封', cls: 'ok' }
  if (b.status === 'sealed') return { text: '已封残留（非现行）', cls: 'bad' }
  if (b.status === 'superseded') return { text: '已被覆盖（只读）', cls: 'bad' }
  return { text: b.status, cls: 'warn' }
}

onMounted(load)

/* align-24 */
function alignHint(batch) {
  if (!batch) return ''
  const v = Number(batch.variance || 0)
  const s = Number(batch.system_total || 0)
  const d = Number(batch.declared_cash || 0)
  return [s, d, v].join('/')
}
function alignRows(rows) {
  return (rows || []).map(r => ({ ...r, align: alignHint(r) }))
}
</script>

<template>
  <h2>日结封账</h2>
  <div v-if="preview" class="panel">
    <p>
      营业日 {{ preview.business_day }} · 笔数 {{ preview.line_count }} · 系统合计 {{ preview.system_total.toFixed(2) }}
      <span class="tag" :class="preview.sealed ? 'ok' : 'warn'">{{ preview.sealed ? '已封' : '未封' }}</span>
      <template v-if="currentBatch">· 现行批 {{ currentBatch.batch_no }}</template>
    </p>
    <p class="msg">
      重封策略：<strong>{{ supersede ? '重算覆盖（supersede）' : '禁止重封' }}</strong>
      <template v-if="preview.sealed && supersede">——再次封账会以当前流水重算，旧批 {{ currentBatch?.batch_no }} 转为 superseded，新批成为唯一现行。</template>
      <template v-else-if="preview.sealed">——已封账一律拒绝重封，以现行批 {{ currentBatch?.batch_no }} 为准，写锁保持。</template>
    </p>
    <div class="row">
      <label>实盘现金 <input type="number" step="0.01" v-model.number="declared" /></label>
      <label v-if="preview.sealed && supersede">覆盖原因（留档）
        <input v-model="reason" placeholder="如：补录漏记流水后重算" />
      </label>
      <button class="btn" :disabled="!canSeal" @click="seal">
        {{ preview.sealed ? '重算覆盖封账' : '封账' }}
      </button>
      <button class="btn ghost" @click="load">刷新</button>
    </div>
  </div>
  <p v-if="msg" class="msg">{{ msg }}</p>
  <p v-if="err" class="msg err">{{ err }}</p>

  <div v-if="preview && preview.history.length" class="panel">
    <h2>当前营业日批次</h2>
    <table>
      <thead><tr><th>批次</th><th>系统</th><th>实盘</th><th>差额</th><th>状态</th><th>被新批取代</th><th>覆盖原因</th></tr></thead>
      <tbody>
        <tr v-for="b in preview.history" :key="b.id">
          <td>{{ b.batch_no }}</td>
          <td>{{ b.system_total.toFixed(2) }}</td>
          <td>{{ b.declared_cash.toFixed(2) }}</td>
          <td>{{ b.variance.toFixed(2) }}</td>
          <td><span class="tag" :class="batchState(b).cls">{{ batchState(b).text }}</span></td>
          <td>{{ b.superseded_by_batch_id ? (batchNoById[b.superseded_by_batch_id] || '#' + b.superseded_by_batch_id) : '—' }}</td>
          <td>{{ b.superseded_reason || '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="panel">
    <h2>全部封账批次</h2>
    <table>
      <thead><tr><th>批次</th><th>营业日</th><th>系统</th><th>实盘</th><th>差额</th><th>状态</th><th>封账时间</th></tr></thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id">
          <td>{{ b.batch_no }}</td><td>{{ b.business_day }}</td>
          <td>{{ b.system_total.toFixed(2) }}</td><td>{{ b.declared_cash.toFixed(2) }}</td>
          <td>{{ b.variance.toFixed(2) }}</td>
          <td><span class="tag" :class="batchState(b).cls">{{ batchState(b).text }}</span></td>
          <td>{{ b.sealed_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
