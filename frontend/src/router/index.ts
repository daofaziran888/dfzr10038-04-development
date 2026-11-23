import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import QualityReleaseView from '@/views/QualityReleaseView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径必须排在动态模块路由之前。
    { path: '/quality_release', name: 'quality_release', component: QualityReleaseView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
