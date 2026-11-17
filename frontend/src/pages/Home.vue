<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const dash = ref(null)
const err = ref('')

onMounted(async () => {
  try { dash.value = await api.dashboard() }
  catch (e) { err.value = e.message }
})
</script>

<template>
  <section class="hero">
    <h1>日封台</h1>
    <p>把当日收银流水钉进营业日封账：系统合计与实盘对齐，封账后流水不可再改。</p>
  </section>
  <p v-if="err" class="msg err">{{ err }}</p>
  <div class="stats" v-if="dash">
    <div class="stat"><div class="label">营业日</div><div class="value">{{ dash.business_day }}</div></div>
    <div class="stat"><div class="label">今日流水笔数</div><div class="value">{{ dash.line_count_today }}</div></div>
    <div class="stat"><div class="label">系统合计</div><div class="value">{{ dash.system_total_today.toFixed(2) }}</div></div>
    <div class="stat">
      <div class="label">封账状态</div>
      <div class="value">{{ dash.sealed_today ? '已封' : '未封' }}</div>
      <small v-if="dash.current_batch_no">现行批 {{ dash.current_batch_no }}</small>
    </div>
    <div class="stat">
      <div class="label">重封策略</div>
      <div class="value">{{ dash.reseal_policy_info?.label }}</div>
      <small>{{ dash.sealed_today ? (dash.reseal_policy === 'supersede' ? '可重算覆盖' : '禁止重封') : '首次封账' }}</small>
    </div>
  </div>
</template>
