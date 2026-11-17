<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const rows = ref([])
const showHistory = ref(false)

async function load() {
  rows.value = showHistory.value ? await api.variances() : await api.variancesCurrent()
}

async function toggle() {
  showHistory.value = !showHistory.value
  await load()
}

onMounted(load)
</script>

<template>
  <h2>长短款</h2>
  <div class="row">
    <button class="btn ghost" @click="toggle">
      {{ showHistory ? '只看现行' : '显示历史（superseded 只读）' }}
    </button>
  </div>
  <div class="panel">
    <table>
      <thead><tr><th>营业日</th><th>批次</th><th>差额</th><th>处置</th><th>现行</th><th>时间</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.business_day }}</td>
          <td>{{ r.batch_no || '—' }}</td>
          <td>{{ r.variance.toFixed(2) }}</td>
          <td><span class="tag" :class="r.disposition === 'balanced' ? 'ok' : 'warn'">{{ r.disposition }}</span></td>
          <td><span class="tag" :class="r.is_current ? 'ok' : 'bad'">{{ r.is_current ? '现行' : '历史' }}</span></td>
          <td>{{ r.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
