<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const day = ref('')
const policy = ref(null)
const msg = ref('')
const err = ref('')

onMounted(async () => {
  const d = await api.getDay()
  day.value = d.business_day
  policy.value = await api.resealPolicy()
})

async function save() {
  msg.value = ''; err.value = ''
  try {
    await api.setDay(day.value)
    msg.value = '已更新营业日'
  } catch (e) { err.value = e.message }
}

async function choosePolicy(value) {
  msg.value = ''; err.value = ''
  if (policy.value && policy.value.policy === value) return
  try {
    const r = await api.setResealPolicy(value)
    policy.value = await api.resealPolicy()
    msg.value = `重封策略已切换为「${r.label}」；已封批次与历史数字不改写，仅影响下一次重封。`
  } catch (e) {
    err.value = e.message
    // 切换失败时回读服务端真实配置，单选按钮回到权威值。
    try { policy.value = await api.resealPolicy() } catch { /* 保留旧值 */ }
  }
}
</script>

<template>
  <h2>设置</h2>
  <div class="row">
    <label>营业日 <input v-model="day" placeholder="YYYY-MM-DD" /></label>
    <button class="btn" @click="save">保存</button>
  </div>

  <div v-if="policy" class="panel">
    <h2>重封权威（全局唯一）</h2>
    <p class="msg">同一营业日已存在现行 sealed 批时，再次封账只按这一种策略裁决。</p>
    <div class="row">
      <label v-for="opt in policy.options" :key="opt.value" class="policy-opt">
        <input
          type="radio"
          name="reseal-policy"
          :value="opt.value"
          :checked="policy.policy === opt.value"
          @change="choosePolicy(opt.value)"
        />
        <span>
          <strong>{{ opt.label }}</strong>
          <small>{{ opt.description }}</small>
        </span>
      </label>
    </div>
    <p class="msg">
      现行批 {{ policy.current_batch_count }} 个 · superseded 历史批 {{ policy.superseded_batch_count }} 个
      <template v-if="policy.policy === 'forbid'">· 已封日再次封账将 409 拒绝并只读返回原批</template>
      <template v-else>· 已封日再次封账将以现行流水重算，旧批转 superseded 只读保留</template>
    </p>
  </div>

  <p v-if="msg" class="msg">{{ msg }}</p>
  <p v-if="err" class="msg err">{{ err }}</p>
</template>
